#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import unittest

from test_draw_auto_research_impl_r1 import *  # noqa: F401,F403


def _test_recovery_slots_are_more_than_batches(self) -> None:
    workflow = (pathlib.Path(__file__).parents[2] / ".github/workflows/football-draw-auto-research-r1.yml").read_text()
    self.assertIn("slot: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]", workflow)


AutoResearchTests.test_recovery_slots_are_more_than_batches = _test_recovery_slots_are_more_than_batches

if __name__ == "__main__":
    unittest.main(verbosity=2)
