from __future__ import annotations

import json
import unittest
from pathlib import Path

from nova_n10_referee_sky_memento_provider_matrix_v1 import (
    SkyMementoProviderMatrixError,
    analyze_timemap,
    build_timemap_url,
    classify,
    parse_link_format,
    redirect_allowed,
)

REG = Path(__file__).with_name("nova_n10_referee_sky_memento_provider_matrix_registry_v1.json")
TARGET = "https://sport.sky.it/calcio/serie-a/2022/09/29/arbitri-serie-a-designazioni-giornata-8"


class T(unittest.TestCase):
    def test_registry_provider_matrix_is_exact(self):
        p = json.loads(REG.read_text())
        self.assertEqual(p["exact_base"], "8e454b16a22b8540deab8fa388d848f762e73b77")
        self.assertEqual(p["memgator_registry_source"]["commit"], "6a222465b44503abe1bf80dc91109cef061a645b")
        self.assertEqual(p["memgator_registry_source"]["git_blob_sha"], "e488ca99091d7eb6efc986524fe7fd9b12807fa5")
        ids = [x["id"] for x in p["providers"]]
        self.assertEqual(ids, p["expected_provider_ids"])
        self.assertEqual(len(ids), 9)
        self.assertFalse(set(ids) & set(p["excluded_prior_providers"]))

    def test_build_timemap_url_keeps_exact_target(self):
        q = build_timemap_url("https://example.test/timemap/link/", TARGET)
        self.assertEqual(q, "https://example.test/timemap/link/" + TARGET)

    def test_parse_link_format(self):
        raw = (
            f'<{TARGET}>; rel="original",\n'
            f'<https://archive.test/20220929120000/{TARGET}>; rel="memento"; datetime="Thu, 29 Sep 2022 12:00:00 GMT",\n'
            f'<https://archive.test/timemap/{TARGET}>; rel="timemap"; type="application/link-format"\n'
        ).encode()
        rows = parse_link_format(raw)
        self.assertEqual(len(rows), 3)
        self.assertIn("original", rows[0]["rel"])
        self.assertIn("memento", rows[1]["rel"])

    def test_analyze_timemap_exact_original_and_pit(self):
        raw = (
            f'<{TARGET}?utm=x>; rel="original",\n'
            f'<https://archive.test/20220929120000/{TARGET}>; rel="first memento"; datetime="Thu, 29 Sep 2022 12:00:00 GMT",\n'
            f'<https://archive.test/20220930120000/{TARGET}>; rel="last memento"; datetime="Fri, 30 Sep 2022 12:00:00 GMT"\n'
        ).encode()
        rows = parse_link_format(raw)
        out = analyze_timemap(
            rows,
            TARGET,
            "2022-09-29T11:00:00Z",
            "2022-09-29T18:00:00Z",
        )
        self.assertEqual(out["exact_original_relation_n"], 1)
        self.assertEqual(out["memento_n"], 2)
        self.assertEqual(out["pit_memento_n"], 1)

    def test_analyze_timemap_rejects_missing_original_identity(self):
        raw = (
            f'<https://wrong.example/x>; rel="original",\n'
            f'<https://archive.test/20220929120000/{TARGET}>; rel="memento"; datetime="Thu, 29 Sep 2022 12:00:00 GMT"\n'
        ).encode()
        with self.assertRaises(SkyMementoProviderMatrixError):
            analyze_timemap(
                parse_link_format(raw),
                TARGET,
                "2022-09-29T11:00:00Z",
                "2022-09-29T18:00:00Z",
            )

    def test_redirect_allowed_only_same_provider_timemap(self):
        provider = {
            "allowed_hosts": ["webarchive.example"],
        }
        self.assertTrue(
            redirect_allowed("https://webarchive.example/new/timemap/link/x", provider)
        )
        self.assertFalse(
            redirect_allowed("https://webarchive.example/replay/2022/x", provider)
        )
        self.assertFalse(
            redirect_allowed("https://other.example/timemap/link/x", provider)
        )

    def test_classify_positive_and_complete_zero(self):
        p = json.loads(REG.read_text())
        c, n = classify(1, 4, 27, p)
        self.assertEqual(c, "POSITIVE_SIGNAL_SOURCE_FEASIBILITY")
        self.assertEqual(n, p["reasonable_subroutes"]["if_positive"])
        c2, n2 = classify(0, 0, 27, p)
        self.assertEqual(c2, "STOP_DATA_COVERAGE")
        self.assertEqual(n2, p["reasonable_subroutes"]["if_complete_zero"])

    def test_classify_external_and_mixed(self):
        p = json.loads(REG.read_text())
        c, n = classify(0, 27, 27, p)
        self.assertEqual(c, "STOP_DATA_COVERAGE")
        self.assertEqual(n, p["reasonable_subroutes"]["if_external_error_only"])
        c2, n2 = classify(0, 3, 27, p)
        self.assertEqual(c2, "STOP_DATA_COVERAGE")
        self.assertEqual(n2, p["reasonable_subroutes"]["if_mixed_zero_and_error"])


if __name__ == "__main__":
    unittest.main()
