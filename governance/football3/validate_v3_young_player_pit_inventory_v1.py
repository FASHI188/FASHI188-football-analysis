#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REMOTE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/dvc/"
HISTORICAL_DIR_MD5 = "c829abceffc9d979752b46b5e1c947bc.dir"
DECLARED_NFILES = 17
MAX_DESCRIPTOR_BYTES = 2_000_000

CATEGORY_TOKENS = {
    "players": ("player",),
    "clubs": ("club", "team"),
    "transfers": ("transfer",),
    "valuations": ("valuation", "market_value", "value"),
    "appearances": ("appearance",),
    "games": ("game", "match"),
    "lineups": ("lineup",),
}
REQUIRED = {"players", "clubs", "transfers"}

PRE_STAGE6_COUNTS = {
    "Bundesliga": 9,
    "EPL": 20,
    "La_liga": 31,
    "Ligue_1": 19,
    "Serie_A": 20,
}


def dvc_descriptor_urls(dir_md5: str):
    if not dir_md5.endswith(".dir"):
        raise ValueError("dir md5 must end in .dir")
    h = dir_md5[:-4]
    if len(h) != 32 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError("invalid md5")
    return [
        f"{REMOTE}files/md5/{h[:2]}/{h[2:]}.dir",
        f"{REMOTE}{h[:2]}/{h[2:]}.dir",
    ]


def fetch_descriptor(url: str):
    if not url.startswith(REMOTE) or not url.endswith(".dir"):
        raise ValueError("only DVC .dir descriptor URLs allowed")
    req = Request(url, headers={"User-Agent": "Football3-young-player-zero-label-audit/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            if getattr(resp, "status", 200) != 200:
                raise RuntimeError(f"HTTP {resp.status}")
            raw = resp.read(MAX_DESCRIPTOR_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"descriptor fetch failed: {exc}") from exc
    if len(raw) > MAX_DESCRIPTOR_BYTES:
        raise RuntimeError("descriptor too large")
    return raw


def parse_descriptor(raw: bytes):
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, list):
        raise ValueError("descriptor must be a list")
    out = []
    for row in obj:
        if not isinstance(row, dict):
            raise ValueError("invalid descriptor row")
        relpath = row.get("relpath")
        md5 = row.get("md5")
        size = row.get("size")
        if not isinstance(relpath, str) or not relpath or relpath.startswith("/") or ".." in Path(relpath).parts:
            raise ValueError("invalid relpath")
        if not isinstance(md5, str) or len(md5.replace(".dir", "")) != 32:
            raise ValueError("invalid md5")
        if size is not None and (not isinstance(size, int) or size < 0):
            raise ValueError("invalid size")
        out.append({"relpath": relpath, "md5": md5, "size": size})
    return out


def categorize(relpaths):
    cats = {k: [] for k in CATEGORY_TOKENS}
    for path in relpaths:
        low = path.lower()
        for cat, tokens in CATEGORY_TOKENS.items():
            if any(tok in low for tok in tokens):
                cats[cat].append(path)
    return {k: sorted(v) for k, v in cats.items() if v}


def run(network=True):
    receipt = {
        "schema_version": "football3-v3-young-player-pit-inventory-receipt-v1",
        "classification": "ZERO_LABEL_HISTORICAL_PIT_INVENTORY_AUDIT",
        "labels_opened": 0,
        "training": False,
        "tuning": False,
        "result_or_goal_values_read": 0,
        "referenced_dvc_data_objects_downloaded": 0,
        "historical_snapshot": {
            "git_commit": "59fa295c51fc23466f3a71542f8bf3d1335daa83",
            "committed_at_utc": "2026-07-11T12:38:11Z",
            "dir_md5": HISTORICAL_DIR_MD5,
            "declared_nfiles": DECLARED_NFILES,
            "attempts": [],
        },
        "confirmation_inventory_upper_bound": {
            "stage6_cutoff_utc": "2026-09-04T11:00:00Z",
            "per_competition": PRE_STAGE6_COUNTS,
            "max_n": sum(PRE_STAGE6_COUNTS.values()),
            "fresh_unconsumed_n": None,
            "freshness_status": "UNRESOLVED_IDENTITY_CONSUMPTION_AUDIT_REQUIRED",
            "stage6_locked_n": 1335,
            "stage6_queue_identity_sha256": "6cfcaba8e2f82af0996a404eb3fc5bb477174aebd09c9b10c7434d95e59c8dfc",
        },
        "data_ready": False,
        "formal_weight": 0,
        "matrix_delta": 0,
    }
    if sum(PRE_STAGE6_COUNTS.values()) != 99:
        raise RuntimeError("frozen pre-Stage6 inventory arithmetic drift")
    if not network:
        receipt["decision"] = "OFFLINE_SYNTHETIC_ONLY"
        return receipt

    raw = None
    used_url = None
    for url in dvc_descriptor_urls(HISTORICAL_DIR_MD5):
        try:
            raw = fetch_descriptor(url)
            used_url = url
            receipt["historical_snapshot"]["attempts"].append({"url": url, "status": "PUBLIC_DESCRIPTOR_OK"})
            break
        except Exception as exc:
            receipt["historical_snapshot"]["attempts"].append({"url": url, "status": "UNAVAILABLE", "error": str(exc)})
    if raw is None:
        receipt["decision"] = "STOP_DATA_COVERAGE_YOUNG_PLAYER_SNAPSHOT_UNAVAILABLE"
        return receipt

    entries = parse_descriptor(raw)
    relpaths = sorted(x["relpath"] for x in entries)
    cats = categorize(relpaths)
    receipt["historical_snapshot"].update({
        "descriptor_url": used_url,
        "descriptor_bytes": len(raw),
        "descriptor_sha256": hashlib.sha256(raw).hexdigest(),
        "entry_count": len(entries),
        "declared_count_matches": len(entries) == DECLARED_NFILES,
        "relpaths": relpaths,
        "categories": cats,
    })
    present = set(cats)
    missing = sorted(REQUIRED - present)
    receipt["historical_snapshot"]["required_categories_present"] = sorted(REQUIRED & present)
    receipt["historical_snapshot"]["required_categories_missing"] = missing

    if len(entries) != DECLARED_NFILES:
        receipt["decision"] = "STOP_DATA_COVERAGE_YOUNG_PLAYER_DESCRIPTOR_CARDINALITY_DRIFT"
    elif missing:
        receipt["decision"] = "STOP_DATA_COVERAGE_YOUNG_PLAYER_ASSET_SURFACE"
    else:
        receipt["decision"] = "PASS_YOUNG_PLAYER_SNAPSHOT_TRANSPORT_NEXT_SCHEMA_IDENTITY_AUDIT"
    return receipt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    receipt = run(network=not args.offline)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "decision": receipt["decision"],
        "labels_opened": receipt["labels_opened"],
        "training": receipt["training"],
        "tuning": receipt["tuning"],
        "result_or_goal_values_read": receipt["result_or_goal_values_read"],
        "data_ready": receipt["data_ready"],
        "max_confirmation_inventory_n": receipt["confirmation_inventory_upper_bound"]["max_n"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
