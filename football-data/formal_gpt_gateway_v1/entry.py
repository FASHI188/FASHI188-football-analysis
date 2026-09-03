#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import source_compat_v2

COMPAT = source_compat_v2.install()
import gateway


def main() -> int:
    code = gateway.main()
    import sys
    try:
        out_arg = sys.argv[sys.argv.index("--out") + 1]
        p = Path(out_arg) / "summary.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            d["source_compat"] = COMPAT
            p.write_bytes(gateway.canon(d))
            (Path(out_arg) / "source_compat.json").write_bytes(gateway.canon(COMPAT))
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
