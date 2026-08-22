#!/usr/bin/env python3
from __future__ import annotations

import ast
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import adaptive_latent_stage4_xg_quarantine_v2 as q


def _call_shape(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return _call_shape(node.value) + "." + node.attr
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute):
            suffix = node.func.attr
        elif isinstance(node.func, ast.Name):
            suffix = node.func.id
        else:
            suffix = "unknown"
        return "<call>." + suffix
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return "<str>"
    if isinstance(node, ast.Subscript):
        return "<subscript>"
    return "<" + type(node).__name__ + ">"


QUARANTINE_IMPORTS = frozenset({
    "__future__", "hashlib", "json", "math", "datetime", "typing",
    "urllib.parse", "adaptive_latent_identity_lock_v1",
})
QUARANTINE_CALLS = frozenset({
    "<call>.astimezone.isoformat",
    "<call>.dumps.encode",
    "<call>.isoformat.replace",
    "<call>.join.encode",
    "<call>.sha256.hexdigest",
    "<str>.join",
    "<subscript>.splitlines",
    "QuarantineError",
    "_exact_keys",
    "_https_source_url",
    "_identity_lock_hash",
    "_nonempty",
    "_nonnegative_finite",
    "_parse_canonical_utc_z",
    "_positive_int",
    "_sha256_hex",
    "_zero_int",
    "_zero_weight",
    "any",
    "bool",
    "build_identity_lock",
    "canonical_target",
    "canonical_xg_projection",
    "datetime.fromisoformat",
    "enumerate",
    "float",
    "frozenset",
    "hashlib.sha256",
    "json.dumps",
    "len",
    "materialize_quarantine_snapshot",
    "math.isfinite",
    "parsed.astimezone",
    "parts.path.startswith",
    "rows.append",
    "rows.sort",
    "seen_fixture.add",
    "seen_provider_event.add",
    "set",
    "sorted",
    "text.endswith",
    "timedelta",
    "type",
    "urlsplit",
    "value.strip",
})

IDENTITY_IMPORTS = frozenset({
    "__future__", "csv", "datetime", "hashlib", "io", "json", "typing",
})
IDENTITY_CALLS = frozenset({
    "<call>.dumps.encode",
    "<call>.isoformat.replace",
    "<call>.join.encode",
    "<call>.sha256.hexdigest",
    "<call>.str.strip",
    "<str>.join",
    "IdentityLockError",
    "_aware",
    "_text",
    "_utc_iso",
    "canon.append",
    "canon.sort",
    "csv.writer",
    "csv_text.encode",
    "enumerate",
    "frozenset",
    "hasattr",
    "hashlib.sha256",
    "ids.append",
    "io.StringIO",
    "isinstance",
    "json.dumps",
    "key",
    "len",
    "out.getvalue",
    "seen.add",
    "set",
    "sorted",
    "str",
    "timedelta",
    "u.isoformat",
    "v.astimezone",
    "v.utcoffset",
    "w.writerow",
})


def _module_surface(path: Path) -> tuple[set[str], set[str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    calls: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module or "")
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            calls.add(_call_shape(node.func))
    return imports, calls


def _z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _target(event_id: int = 101) -> dict:
    kickoff = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)
    return {
        "competition_id": "ENG_PremierLeague",
        "fixture_id": f"fixture-projection:{event_id}",
        "provider_event_id": event_id,
        "kickoff_at": _z(kickoff),
        "home_team_id": "fixture-projection:1",
        "away_team_id": "fixture-projection:2",
        "prediction_cutoff": _z(kickoff - timedelta(minutes=15)),
    }


def _projection(target: dict) -> dict:
    at = datetime(2026, 8, 20, 22, 0, tzinfo=timezone.utc)
    return {
        "schema": q.PROJECTION_SCHEMA,
        "source_kind": q.PROJECTION_SOURCE_KIND,
        "source_identity": "synthetic",
        "source_url": "https://example.invalid/statistics/101",
        "payload_sha256": "a" * 64,
        "collector_run_id": "synthetic-run",
        "provider_event_id": target["provider_event_id"],
        "target_identity_sha256": q._identity_lock_hash(q.canonical_target(target)),
        "home_xg": 1.2,
        "away_xg": 0.8,
        "collector_first_observed_at": _z(at),
        "retrieved_at": _z(at + timedelta(seconds=1)),
        "ingested_at": _z(at + timedelta(seconds=2)),
        "raw_provider_payload_persisted": False,
        "real_labels_read": 0,
        "completion_status": q.UNKNOWN_COMPLETION,
        "final_xg_status": q.UNKNOWN_FINAL_XG,
        "formal_pit_eligible": False,
        "eligible_for_latent_update": False,
        "formal_weight": 0.0,
        "research_only": True,
    }


class IndependentAdversarialV2(unittest.TestCase):
    def test_quarantine_ast_surface_is_exact_whitelist(self):
        imports, calls = _module_surface(HERE / "adaptive_latent_stage4_xg_quarantine_v2.py")
        self.assertEqual(imports, set(QUARANTINE_IMPORTS))
        self.assertEqual(calls, set(QUARANTINE_CALLS))

    def test_identity_lock_ast_surface_is_exact_whitelist(self):
        imports, calls = _module_surface(HERE / "adaptive_latent_identity_lock_v1.py")
        self.assertEqual(imports, set(IDENTITY_IMPORTS))
        self.assertEqual(calls, set(IDENTITY_CALLS))

    def test_benign_unknown_field_is_denied(self):
        t = _target()
        p = _projection(t)
        p["telemetry"] = "innocent"
        with self.assertRaises(q.QuarantineError):
            q.materialize_quarantine_snapshot(t, p)

    def test_nested_object_in_numeric_slot_is_denied(self):
        t = _target()
        p = _projection(t)
        p["home_xg"] = {"value": 1.2}
        with self.assertRaises(q.QuarantineError):
            q.materialize_quarantine_snapshot(t, p)

    def test_plain_type_contract_rejects_int_dict_and_list_subclasses(self):
        class IntSub(int):
            pass
        class DictSub(dict):
            pass
        class ListSub(list):
            pass
        t = _target()
        p = _projection(t)
        with self.assertRaises(q.QuarantineError):
            q.materialize_quarantine_snapshot({**t, "provider_event_id": IntSub(101)}, p)
        with self.assertRaises(q.QuarantineError):
            q.materialize_quarantine_snapshot(DictSub(t), p)
        with self.assertRaises(q.QuarantineError):
            q.materialize_quarantine_batch(ListSub([{"target": t, "projection": p}]))

    def test_equivalent_completion_and_activation_claims_are_denied(self):
        t = _target()
        for patch in (
            {"completion_status": "FINISHED"},
            {"final_xg_status": "FINAL"},
            {"formal_pit_eligible": True},
            {"eligible_for_latent_update": True},
            {"formal_weight": 1e-12},
        ):
            p = _projection(t)
            p.update(patch)
            with self.subTest(patch=patch):
                with self.assertRaises(q.QuarantineError):
                    q.materialize_quarantine_snapshot(t, p)

    def test_very_late_snapshot_still_cannot_claim_completion(self):
        t = _target()
        p = _projection(t)
        p["collector_first_observed_at"] = "2027-01-01T00:00:00Z"
        p["retrieved_at"] = "2027-01-01T00:00:01Z"
        p["ingested_at"] = "2027-01-01T00:00:02Z"
        row = q.materialize_quarantine_snapshot(t, p)
        self.assertEqual(row["completion_status"], q.UNKNOWN_COMPLETION)
        self.assertFalse(row["formal_pit_eligible"])
        self.assertFalse(row["eligible_for_latent_update"])
        self.assertNotIn("event_completed_at", row)

    def test_url_query_and_fragment_are_denied_even_without_dangerous_names(self):
        t = _target()
        for url in (
            "https://example.invalid/statistics/101?x=1",
            "https://example.invalid/statistics/101#x",
        ):
            p = _projection(t)
            p["source_url"] = url
            with self.subTest(url=url):
                with self.assertRaises(q.QuarantineError):
                    q.materialize_quarantine_snapshot(t, p)


if __name__ == "__main__":
    unittest.main(verbosity=2)
