#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

# Pure engineering fix before execution: pandas DataFrame.T is transpose,
# while the frozen scientific target column is literally named `T`.
source_path=Path(__file__).with_name("evaluate_c072i2_dgiven_t_forward_confirm.py")
source=source_path.read_text(encoding="utf-8")
source=source.replace("scored.T.map", "scored['T'].map")
source=source.replace("even.T//2", "even['T']//2")
ns={"__name__":"c072i2_fixed","__file__":str(source_path)}
exec(compile(source,str(source_path),"exec"),ns)

if __name__=="__main__":
    ns["main"]()
