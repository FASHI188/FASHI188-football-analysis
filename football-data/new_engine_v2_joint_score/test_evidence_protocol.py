import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from evidence_protocol import (
    assert_label_free_json,
    build_blind_freeze_package,
    record_v1_artifact_audit,
    sha256_file,
    verify_manifest_tree,
    verify_zip,
)


class EvidenceProtocolTests(unittest.TestCase):
    def _fixture(self, root: Path):
        model = root / "engine_fixture.py"
        model.write_text("FROZEN_RULE = 1\n", encoding="utf-8")
        identity = root / "identity.jsonl"
        identity.write_text(
            json.dumps({"match_id": "m1", "kickoff": "2030-01-01T00:00:00Z", "home": "A", "away": "B"}) + "\n",
            encoding="utf-8",
        )
        pred = root / "pred.jsonl"
        pred.write_text(
            json.dumps({"match_id": "m1", "p_home": 0.4, "p_draw": 0.3, "p_away": 0.3}) + "\n",
            encoding="utf-8",
        )
        predictor = root / "predictor_contract.json"
        predictor.write_text(json.dumps({"allowed_fields": ["match_id", "kickoff", "home", "away"]}), encoding="utf-8")
        scorer = root / "scorer_boundary.json"
        scorer.write_text(json.dumps({"prediction_fields": ["match_id", "p_home", "p_draw", "p_away"], "labels_read_only_after_verified_artifact": True}), encoding="utf-8")
        rules = root / "rules.json"
        rules.write_text(json.dumps({"score_formula": "frozen", "promotion_gates": ["frozen-gate"]}), encoding="utf-8")
        return model, identity, pred, predictor, scorer, rules

    def test_synthetic_blind_freeze_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model, identity, pred, predictor, scorer, rules = self._fixture(root)
            out = root / "artifact"
            result = build_blind_freeze_package(
                out,
                head="1" * 40,
                parent="2" * 40,
                model_rule_files=[model],
                holdout_identity=identity,
                blind_predictions=pred,
                predictor_input_contract=predictor,
                scorer_input_boundary=scorer,
                final_rules_and_gates=rules,
            )
            self.assertGreaterEqual(result["payload_count"], 7)
            tree = verify_manifest_tree(out)
            self.assertTrue(tree["ok"], tree)
            freeze = json.loads((out / "blind_freeze.json").read_text())
            self.assertEqual(freeze["status"], "BLIND_FREEZE_LABELS_STILL_SEALED")
            self.assertFalse(freeze["label_file_opened"])
            self.assertFalse(freeze["scorer_invoked"])
            self.assertEqual(freeze["exact_head"], "1" * 40)
            self.assertEqual(freeze["direct_parent"], "2" * 40)

            zip_path = root / "artifact.zip"
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for p in sorted(out.rglob("*")):
                    if p.is_file():
                        zf.write(p, p.relative_to(out).as_posix())
            checked = verify_zip(zip_path)
            self.assertTrue(checked["ok"], checked)
            self.assertTrue(checked["crc_ok"])
            self.assertEqual(checked["missing"], [])
            self.assertEqual(checked["mismatch"], [])
            self.assertEqual(checked["extra"], [])
            self.assertEqual(checked["zip_sha256"], sha256_file(zip_path))

    def test_manifest_detects_tamper_and_extra(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            model, identity, pred, predictor, scorer, rules = self._fixture(root)
            out = root / "artifact"
            build_blind_freeze_package(
                out,
                head="a" * 40,
                parent="b" * 40,
                model_rule_files=[model],
                holdout_identity=identity,
                blind_predictions=pred,
                predictor_input_contract=predictor,
                scorer_input_boundary=scorer,
                final_rules_and_gates=rules,
            )
            (out / "payload" / "blind_predictions.jsonl").write_text("{}\n", encoding="utf-8")
            (out / "unexpected.txt").write_text("extra", encoding="utf-8")
            checked = verify_manifest_tree(out)
            self.assertFalse(checked["ok"])
            self.assertIn("payload/blind_predictions.jsonl", checked["mismatch"])
            self.assertIn("unexpected.txt", checked["extra"])

    def test_label_bearing_blind_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "bad.jsonl"
            p.write_text(json.dumps({"match_id": "m1", "actual_outcome": "H"}) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                assert_label_free_json(p)

    def test_v1_audit_preserves_full_digest_strings_and_raw_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            zip_path = root / "v1.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("x.txt", "x")
            expected = "sha256:" + sha256_file(zip_path)
            raw = root / "metadata_raw.json"
            raw_text = json.dumps({"id": 9732754224, "name": "v1", "digest": expected, "size_in_bytes": zip_path.stat().st_size}, separators=(",", ":"))
            raw.write_text(raw_text, encoding="utf-8")
            output = root / "audit.json"
            report = record_v1_artifact_audit(
                raw_metadata_file=raw,
                expected_digest=expected,
                zip_path=zip_path,
                output_file=output,
            )
            self.assertEqual(raw.read_text(encoding="utf-8"), raw_text)
            self.assertEqual(report["expected_digest"], expected)
            self.assertEqual(report["actual_digest"], expected)
            self.assertEqual(report["expected_digest_length"], len(expected))
            self.assertEqual(report["actual_digest_length"], len(expected))
            self.assertEqual(report["downloaded_zip_sha256"], expected)
            self.assertTrue(report["digest_exact_string_match"])


if __name__ == "__main__":
    unittest.main()
