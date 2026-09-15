#!/usr/bin/env python3
import importlib.util
import pathlib
import unittest

P = pathlib.Path(__file__).with_name('nova_n1_understat_schema_probe_v1.py')
spec = importlib.util.spec_from_file_location('probe', P)
probe = importlib.util.module_from_spec(spec); spec.loader.exec_module(probe)

class Tests(unittest.TestCase):
    def test_keys_only_scanner(self):
        raw=b'{"teams":{"1":{"history":[{"date":"2024-08-17","deep":7,"ppda":{"att":10,"def":2},"result":"w","xG":2.1}]}}}'
        counts=probe.scan_object_keys_only(raw)
        self.assertEqual(counts['teams'],1)
        self.assertEqual(counts['history'],1)
        self.assertEqual(counts['date'],1)
        self.assertEqual(counts['deep'],1)
        self.assertEqual(counts['ppda'],1)
        self.assertEqual(counts['result'],1)
        self.assertEqual(counts['xG'],1)
        self.assertNotIn('2024-08-17', counts)
        self.assertNotIn('w', counts)

    def test_escaped_key(self):
        raw=b'{"a\\u0062":1,"v":"secret"}'
        counts=probe.scan_object_keys_only(raw)
        self.assertEqual(counts['ab'],1)
        self.assertNotIn('secret',counts)

    def test_unterminated_fails(self):
        with self.assertRaises(ValueError):
            probe.scan_object_keys_only(b'{"abc')

    def test_required_feature_key_contract(self):
        self.assertEqual(probe.REQUIRED_FEATURE_KEYS, {'deep','ppda','date'})
        self.assertIn('result', probe.FORBIDDEN_RESULT_KEYS)
        self.assertIn('xG', probe.FORBIDDEN_RESULT_KEYS)

if __name__=='__main__': unittest.main()
