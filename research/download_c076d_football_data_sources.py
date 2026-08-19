#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
from urllib.request import Request, urlopen

CODES=["E1","E2","E3","SC0","SC1","SC2","SC3","D2","I2","SP2","F2","P1"]
BASE="https://www.football-data.co.uk/mmz4281/2526"

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--out-dir',required=True); a=ap.parse_args()
    out=Path(a.out_dir); out.mkdir(parents=True,exist_ok=True)
    for code in CODES:
        url=f"{BASE}/{code}.csv"
        req=Request(url,headers={'User-Agent':'Mozilla/5.0'})
        with urlopen(req,timeout=60) as r:
            data=r.read()
        if len(data)<100:
            raise RuntimeError(f"source too small: {code} bytes={len(data)}")
        (out/f"{code}.csv").write_bytes(data)
        print(code,len(data))
    return 0

if __name__=='__main__': raise SystemExit(main())
