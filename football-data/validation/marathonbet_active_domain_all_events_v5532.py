#!/usr/bin/env python3
from __future__ import annotations

import marathonbet_active_domain_capture_v5532 as capture

# The generic Football page is a popularity subset. The first-party all-events
# page is the complete prematch category surface and remains a direct Marathonbet
# source. All existing V5.5.32 identity, time, market and immutability gates stay
# unchanged.
capture.BROAD_URL = "https://www.marathonbet.com/en/all-events.htm"


if __name__ == "__main__":
    raise SystemExit(capture.main())
