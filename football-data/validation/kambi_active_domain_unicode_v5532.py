#!/usr/bin/env python3
from __future__ import annotations

from active_domain_unicode_path_v5532 import safe
import kambi_active_domain_capture_v5532 as runner

# Filename safety only. No identity, line, price or probability transformation.
runner.safe = safe


if __name__ == "__main__":
    raise SystemExit(runner.main())
