#!/usr/bin/env python3
"""Summarize the guard's block log and mark entries as false positives.

  report.py [--file PATH] summary                 blocks per rule and per day
  report.py [--file PATH] list [--unreviewed]     entries with ids and redacted commands
  report.py [--file PATH] mark ID [--verdict false-positive|true-block] [--note TEXT]

The log defaults to ${CLAUDE_PLUGIN_DATA}/blocks.jsonl. Marks are appended to the same
file; the last mark for an id wins. See blocklog.py for what is recorded.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter, OrderedDict

import blocklog

VERDICTS = ("false-positive", "true-block")


def load(path):
    blocks, verdicts = OrderedDict(), {}
    try:
        f = open(path, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return blocks, verdicts
    with f:
        for line in f:
            try:
                entry = json.loads(line)
                kind, ident = entry["type"], entry["id"]
            except (ValueError, KeyError, TypeError):
                continue
            if not isinstance(ident, str):
                continue
            if kind == "block":
                # coerce so a wrong-shaped entry cannot crash the report
                for key in ("ts", "rule"):
                    if not isinstance(entry.get(key), str):
                        entry[key] = "?"
                blocks[ident] = entry
            elif kind == "mark":
                verdicts[ident] = entry.get("verdict")
    return blocks, verdicts


def tally(rows):
    """rows: iterable of (key, verdict). Returns {key: [blocks, false_pos, true_block, unreviewed]}."""
    out = {}
    for key, verdict in rows:
        counts = out.setdefault(key, [0, 0, 0, 0])
        counts[0] += 1
        counts[{"false-positive": 1, "true-block": 2}.get(verdict, 3)] += 1
    return out


def table(title, totals):
    print(f"{title:<24}{'blocks':>7}{'fp':>5}{'true':>6}{'open':>6}")
    for key in sorted(totals):
        print(f"{key:<24}" + "".join(f"{n:>{w}}" for n, w in zip(totals[key], (7, 5, 6, 6))))


def summary(blocks, verdicts):
    rows = [(e.get("rule", "?"), verdicts.get(i)) for i, e in blocks.items()]
    by_rule = tally(rows)
    table("rule", by_rule)
    print()
    table("day", tally((e.get("ts", "?")[:10], verdicts.get(i)) for i, e in blocks.items()))
    print()
    totals = Counter()
    for counts in by_rule.values():
        totals.update(dict(enumerate(counts)))
    print(f"{'total':<24}" + "".join(f"{totals[n]:>{w}}" for n, w in zip(range(4), (7, 5, 6, 6))))


def listing(blocks, verdicts, unreviewed):
    for ident, e in blocks.items():
        verdict = verdicts.get(ident)
        if unreviewed and verdict:
            continue
        print(f"{ident}  {e.get('ts', '?')}  {e.get('rule', '?'):<22} {verdict or 'open':<15} "
              f"[{e.get('cwd')}] {e.get('cmd')}")


def mark(path, blocks, ident, verdict, note):
    if ident not in blocks:
        print(f"report: no block with id {ident} in {path}", file=sys.stderr)
        return 1
    entry = {"type": "mark", "id": ident, "verdict": verdict,
             "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if note:
        entry["note"] = note[:blocklog.CMD_LIMIT]
    try:
        with open(path, "rb+") as f:
            f.seek(0, os.SEEK_END)
            needs_newline = f.tell() > 0 and (f.seek(-1, os.SEEK_END), f.read(1))[1] != b"\n"
            f.write((b"\n" if needs_newline else b"") + json.dumps(entry).encode() + b"\n")
    except OSError as error:
        print(f"report: cannot write {path}: {error.strerror}", file=sys.stderr)
        return 1
    print(f"marked {ident} ({blocks[ident].get('rule')}) as {verdict}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--file", help="log file (default: ${CLAUDE_PLUGIN_DATA}/blocks.jsonl)")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("summary")
    p_list = sub.add_parser("list")
    p_list.add_argument("--unreviewed", action="store_true")
    p_mark = sub.add_parser("mark")
    p_mark.add_argument("id")
    p_mark.add_argument("--verdict", choices=VERDICTS, default="false-positive")
    p_mark.add_argument("--note", default="")
    args = parser.parse_args(argv)

    path = args.file or blocklog.log_path()
    if not path:
        print("report: CLAUDE_PLUGIN_DATA is not set; pass --file PATH", file=sys.stderr)
        return 2
    blocks, verdicts = load(path)
    if args.cmd == "list":
        listing(blocks, verdicts, args.unreviewed)
    elif args.cmd == "mark":
        return mark(path, blocks, args.id, args.verdict, args.note)
    else:
        summary(blocks, verdicts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
