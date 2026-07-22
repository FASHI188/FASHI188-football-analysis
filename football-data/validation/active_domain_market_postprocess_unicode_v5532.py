#!/usr/bin/env python3
from __future__ import annotations

from active_domain_unicode_path_v5532 import safe
import active_domain_market_postprocess_v5532 as runner

# The imported V5.5.28/V5.5.29 modules look up ``safe`` at execution time.
# Patch filename generation only; all market and promotion gates are unchanged.
runner.multiline.safe = safe
runner.exact_line.safe = safe


if __name__ == "__main__":
    raise SystemExit(runner.main())
