#!/usr/bin/env python3
"""Exact E3e/E3d baseline adapter for the E3f-2A ablation."""
from __future__ import annotations

import e3f2a_internal_pit_ablation as experiment


def reconstruct_with_registered_baselines():
    base_rows, lineage = experiment.e3e0.e3d1.build_records()
    rows, e3d1_folds = experiment.e3e0.e3d1.expanding_oos(base_rows)
    return rows, {**lineage, "e3d1_folds": e3d1_folds}


experiment.e3f0_entry.audit.reconstruct_fixed_sample = reconstruct_with_registered_baselines

if __name__ == "__main__":
    raise SystemExit(experiment.main())
