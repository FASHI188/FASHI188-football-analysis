#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

import source_compat

COMPAT = source_compat.install()
import gateway


def main() -> int:
    code = gateway.main()
    # gateway.main writes canonical summary into --out; enrich the durable summary with the
    # explicit adapter identity without altering the original prediction receipt.
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
        # Never convert a runner result into a success/failure based only on summary decoration.
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
