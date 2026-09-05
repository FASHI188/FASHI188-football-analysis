#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
from unittest import mock

import formal_frozen_xg_identity_adjudication_v2 as identity
import live_gateway_patch_v1 as live_gateway
import runtime as rt


def main() -> int:
    original_loader = rt.load_xg_labels
    original_delta = rt.history_delta_events
    original_validate = rt._validate_delta_records
    original_apply = rt._apply_events
    try:
        rt.load_xg_labels = lambda *args, **kwargs: ({}, {})
        receipt = identity.install()
        assert receipt["installed"] is True
        assert rt.load_xg_labels is identity._load_xg_labels
        assert rt.history_delta_events is identity.contract._history_delta_events
        assert rt._validate_delta_records is identity.contract._validate_delta_records

        cutoff = rt._parse_dt("2026-09-04T16:00:00Z", "test cutoff")
        expected = {"loaded": {"source": "governed-integrity"}}
        with mock.patch.object(
            live_gateway.integrity_full_rebuild,
            "build_integrity_base",
            return_value=expected,
        ) as build:
            actual = live_gateway._build_frozen_base(
                Path("bundle"), Path("repo"), Path("understat"), Path("confirmation"), cutoff
            )
        assert actual == expected
        build.assert_called_once_with(
            Path("bundle"), Path("repo"), Path("understat"), Path("confirmation"), cutoff
        )
        print("ENTRY_ADJUDICATION_WIRING_PASS")
        return 0
    finally:
        rt.load_xg_labels = original_loader
        rt.history_delta_events = original_delta
        rt._validate_delta_records = original_validate
        rt._apply_events = original_apply


if __name__ == "__main__":
    raise SystemExit(main())
