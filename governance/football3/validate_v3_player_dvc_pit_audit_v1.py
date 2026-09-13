#!/usr/bin/env python3
import argparse, hashlib, json
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

REMOTE = "https://pub-e682421888d945d684bcae8890b0ec20.r2.dev/dvc/"
MAX_DESCRIPTOR_BYTES = 2_000_000
CURRENT_CANARY = {
    "name": "transfermarkt-scraper-current-canary",
    "dir_md5": "c9ec80fd8b18310f7bded68fe92b67d3.dir",
    "declared_nfiles": 74,
}
SNAPSHOTS = [
    {
        "name": "transfermarkt-scraper-2023-12-12",
        "git_commit": "8e763b3f2b760269b5e3ce5d65c94af4f3ec0970",
        "committed_at": "2023-12-12T04:42:01Z",
        "dir_md5": "91552aec889cc33c803e6dcd471b333b.dir",
        "declared_nfiles": 49,
    },
    {
        "name": "transfermarkt-api-2023-12-12",
        "git_commit": "042c2c0f44c38787a1928ccaecf3ad3b39d1070e",
        "committed_at": "2023-12-12T04:44:30Z",
        "dir_md5": "4b6f0b5c5a33b9dea90ad761cc7cb116.dir",
        "declared_nfiles": 12,
    },
]
CATEGORY_TOKENS = {
    "players": ("player",),
    "clubs": ("club", "team"),
    "transfers": ("transfer",),
    "valuations": ("valuation", "value"),
    "appearances": ("appearance",),
    "games": ("game", "match"),
    "lineups": ("lineup",),
    "injuries": ("injur", "sidelined", "absence"),
}
RELEVANT_CATEGORIES = {"players", "clubs", "transfers", "valuations", "appearances", "games", "lineups"}

def descriptor_urls(dir_md5: str):
    if not dir_md5.endswith(".dir"):
        raise ValueError("directory md5 must end in .dir")
    h = dir_md5[:-4]
    if len(h) != 32 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError("invalid md5")
    return [
        f"{REMOTE}files/md5/{h[:2]}/{h[2:]}.dir",
        f"{REMOTE}{h[:2]}/{h[2:]}.dir",
    ]

def parse_descriptor_bytes(raw: bytes):
    if len(raw) > MAX_DESCRIPTOR_BYTES:
        raise ValueError("descriptor too large")
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, list):
        raise ValueError("DVC .dir descriptor must be a list")
    rows = []
    for item in obj:
        if not isinstance(item, dict):
            raise ValueError("invalid descriptor entry")
        relpath = item.get("relpath")
        md5 = item.get("md5")
        size = item.get("size")
        if not isinstance(relpath, str) or not relpath or relpath.startswith("/") or ".." in Path(relpath).parts:
            raise ValueError("invalid relpath")
        if not isinstance(md5, str) or len(md5.replace(".dir", "")) != 32:
            raise ValueError("invalid entry md5")
        if size is not None and (not isinstance(size, int) or size < 0):
            raise ValueError("invalid size")
        rows.append({"relpath": relpath, "md5": md5, "size": size})
    return rows

def categorize(relpaths):
    out = {k: [] for k in CATEGORY_TOKENS}
    for p in relpaths:
        low = p.lower()
        for cat, tokens in CATEGORY_TOKENS.items():
            if any(t in low for t in tokens):
                out[cat].append(p)
    return {k: sorted(v) for k, v in out.items() if v}

def fetch_one(url):
    if not url.startswith(REMOTE) or not url.endswith(".dir"):
        raise ValueError("non-descriptor URL forbidden")
    req = Request(url, headers={"User-Agent": "Football3-zero-label-DVC-audit/1.1"})
    try:
        with urlopen(req, timeout=30) as r:
            if getattr(r, "status", 200) != 200:
                raise RuntimeError(f"HTTP {r.status}")
            raw = r.read(MAX_DESCRIPTOR_BYTES + 1)
    except (HTTPError, URLError, TimeoutError) as e:
        raise RuntimeError(f"descriptor fetch failed: {e}") from e
    if len(raw) > MAX_DESCRIPTOR_BYTES:
        raise RuntimeError("descriptor exceeds byte cap")
    return raw

def fetch_descriptor(dir_md5):
    attempts = []
    for url in descriptor_urls(dir_md5):
        try:
            return fetch_one(url), url, attempts
        except Exception as e:
            attempts.append({"url": url, "error": str(e)})
    raise RuntimeError(json.dumps(attempts, sort_keys=True))

def inspect_snapshot(s, network=True):
    row = {k: s[k] for k in s if k != "declared_size"}
    row["candidate_urls"] = descriptor_urls(s["dir_md5"])
    if not network:
        row["status"] = "NOT_FETCHED_SYNTHETIC"
        return row, set(), False
    try:
        raw, resolved_url, prior_errors = fetch_descriptor(s["dir_md5"])
        entries = parse_descriptor_bytes(raw)
        cats = categorize([x["relpath"] for x in entries])
        row.update({
            "status": "PUBLIC_DESCRIPTOR_OK",
            "resolved_url": resolved_url,
            "prior_attempt_errors": prior_errors,
            "descriptor_bytes": len(raw),
            "descriptor_sha256": hashlib.sha256(raw).hexdigest(),
            "entry_count": len(entries),
            "declared_count_matches": len(entries) == s["declared_nfiles"],
            "relpaths": sorted(x["relpath"] for x in entries),
            "categories": cats,
        })
        return row, set(cats) & RELEVANT_CATEGORIES, len(entries) != s["declared_nfiles"]
    except Exception as e:
        row.update({"status": "DESCRIPTOR_UNAVAILABLE", "error": str(e)})
        return row, set(), True

def run(network=True):
    receipt = {
        "schema_version": "football3-v3-player-dvc-pit-audit-receipt-v1.1",
        "classification": "ZERO_LABEL_DVC_HISTORY_AVAILABILITY_AUDIT",
        "labels_opened": 0,
        "training": False,
        "tuning": False,
        "downloaded_data_objects": 0,
        "snapshots": [],
    }
    canary_row, _, canary_error = inspect_snapshot(CURRENT_CANARY, network=network)
    receipt["current_canary"] = canary_row
    any_error = False
    relevant_union = set()
    for s in SNAPSHOTS:
        row, cats, error = inspect_snapshot(s, network=network)
        relevant_union.update(cats)
        any_error = any_error or error
        receipt["snapshots"].append(row)
    if network and canary_error:
        receipt["decision"] = "STOP_DATA_COVERAGE_DVC_REMOTE_CANARY_UNRESOLVED"
    elif any_error:
        receipt["decision"] = "STOP_DATA_COVERAGE_DVC_OBJECT_UNAVAILABLE"
    elif not relevant_union:
        receipt["decision"] = "STOP_DATA_COVERAGE_PLAYER_ASSET_SURFACE"
    else:
        receipt["decision"] = "PASS_DVC_HISTORY_TRANSPORT_NEXT_SCHEMA_AUDIT"
    receipt["relevant_categories"] = sorted(relevant_union)
    receipt["data_ready"] = False
    receipt["formal_weight"] = 0
    receipt["matrix_delta"] = 0
    return receipt

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    receipt = run(network=not args.offline)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: receipt[k] for k in ("decision", "labels_opened", "training", "tuning", "data_ready")}))

if __name__ == "__main__":
    main()
