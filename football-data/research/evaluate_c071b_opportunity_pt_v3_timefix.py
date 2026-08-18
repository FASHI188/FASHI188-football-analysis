#!/usr/bin/env python3
from __future__ import annotations

import pandas as pd
import evaluate_c071b_opportunity_pt_v2 as core


def utc_ns(x):
    z = pd.to_datetime(x, utc=True, errors="coerce")
    # Pandas/Arrow may preserve timestamp[us]. The C071-B contract compares
    # result availability (kickoff+105m) and target kickoff by integer time;
    # therefore force a single explicit unit before any arithmetic/searchsorted.
    if isinstance(z, pd.Series):
        return z.dt.as_unit("ns")
    if isinstance(z, pd.DatetimeIndex):
        return z.as_unit("ns")
    return z


core.utc = utc_ns

if __name__ == "__main__":
    core.main()
