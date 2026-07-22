#!/usr/bin/env python3
from __future__ import annotations

from active_domain_unicode_path_v5532 import safe
import marathonbet_active_domain_capture_v5532 as base

# Patch only the filename-token function used by the active-domain modules.
# Canonical identities and all market values remain unchanged.
base.safe = safe

import marathonbet_active_domain_direct_v5532 as direct

direct.base.safe = safe

import marathonbet_active_domain_html_v5532 as runner

runner.base.safe = safe
runner.direct.base.safe = safe


if __name__ == "__main__":
    raise SystemExit(runner.main())
