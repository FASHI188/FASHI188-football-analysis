#!/usr/bin/env python3
"""Idempotent test alignment for the grade345 direct-total revision."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    path = ROOT / "tests" / "test_v462_bottom_invariants.py"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        'self.assertEqual(first["direct_total_method"], "geometric_shrunk_venue_total_rates")',
        'self.assertEqual(first["direct_total_method"], "nested_oos_shrunk_geometric_venue_total_rates")\n        self.assertAlmostEqual(first["direct_total_signal_weight"], 1.0, places=12)',
    )
    path.write_text(text, encoding="utf-8")
    print("Aligned V4.6.2 bottom invariant test with nested-OOS direct-total shrinkage.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
