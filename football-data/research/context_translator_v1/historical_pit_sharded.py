from __future__ import annotations

import argparse
import json
import pathlib

import historical_pit_sharded_common as common
import historical_pit_sharded_predict as prediction
import historical_pit_sharded_source as source


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("--v2", type=pathlib.Path, required=True)
    p.add_argument("--out", type=pathlib.Path, required=True)
    s = sub.add_parser("source-freeze")
    s.add_argument("--base", type=pathlib.Path, required=True)
    s.add_argument("--shard", type=int, required=True)
    s.add_argument("--out", type=pathlib.Path, required=True)
    q = sub.add_parser("predict-shard")
    q.add_argument("--base", type=pathlib.Path, required=True)
    q.add_argument("--source", type=pathlib.Path, required=True)
    q.add_argument("--out", type=pathlib.Path, required=True)
    m = sub.add_parser("merge")
    m.add_argument("--base", type=pathlib.Path, required=True)
    m.add_argument("--sources", type=pathlib.Path, required=True)
    m.add_argument("--predictions", type=pathlib.Path, required=True)
    m.add_argument("--out", type=pathlib.Path, required=True)
    f = sub.add_parser("score-final")
    f.add_argument("--merged", type=pathlib.Path, required=True)
    f.add_argument("--label-vault", type=pathlib.Path, required=True)
    f.add_argument("--out", type=pathlib.Path, required=True)
    f.add_argument("--head", required=True)
    f.add_argument("--parent", required=True)
    f.add_argument("--run-id", type=int, required=True)
    f.add_argument("--changed-paths", type=pathlib.Path, required=True)
    args = parser.parse_args()
    if args.cmd == "prepare":
        result = common.prepare(args.v2, args.out)
    elif args.cmd == "source-freeze":
        result = source.source_freeze(args.base, args.shard, args.out)
    elif args.cmd == "predict-shard":
        result = prediction.predict_shard(args.base, args.source, args.out)
    elif args.cmd == "merge":
        result = prediction.merge(args.base, args.sources, args.predictions, args.out)
    else:
        changed_paths = [x for x in args.changed_paths.read_text(encoding="utf-8").splitlines() if x]
        result = prediction.score_final(args.merged, args.label_vault, args.out, head=args.head, parent=args.parent, run_id=args.run_id, changed_paths=changed_paths)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
