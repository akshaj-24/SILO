"""Command-line interface for local SILO Score queries.

Examples::

    # Score a query passed as a JSON string
    python -m idscore.cli query '{"AGEGRP": 1, "GENDER": "Man+", "PR": 35}'

    # Read the query from a file or stdin
    python -m idscore.cli query @myquery.json
    echo '{"AGEGRP": [1,2]}' | python -m idscore.cli query -

    # Documentation (top N fields, default all)
    python -m idscore.cli docs --n 10
    python -m idscore.cli docs --n 10 --format markdown

    # List fields / show one field's codes
    python -m idscore.cli fields
    python -m idscore.cli field AGEGRP
"""
from __future__ import annotations

import argparse
import json
import sys

from .docs import build_documentation, documentation_markdown
from .engine import load_engine
from .metadata import load_metadata
from .query import QueryValidationError, parse_query


def _load_payload(arg: str) -> dict:
    if arg == "-":
        raw = sys.stdin.read()
    elif arg.startswith("@"):
        with open(arg[1:], encoding="utf-8") as fh:
            raw = fh.read()
    else:
        raw = arg
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        print(json.dumps({"error": "invalid_json", "message": str(e)}, indent=2))
        sys.exit(2)


def cmd_query(args) -> int:
    meta = load_metadata()
    payload = _load_payload(args.payload)
    try:
        q = parse_query(payload, meta)
    except QueryValidationError as e:
        print(json.dumps(e.as_dict(), indent=2, ensure_ascii=False))
        return 1
    engine = load_engine()
    result = engine.run(q)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def cmd_docs(args) -> int:
    meta = load_metadata()
    if args.format == "markdown":
        print(documentation_markdown(meta, args.n))
    else:
        print(json.dumps(build_documentation(meta, args.n), indent=2, ensure_ascii=False))
    return 0


def cmd_fields(args) -> int:
    meta = load_metadata()
    for f in meta.importance_order:
        fd = meta.field(f)
        print(f"{fd['importance_rank']:>2}  {f:16}  "
              f"card={fd['cardinality']:>3}  H={fd['entropy_bits']:.2f}  {fd['label']}")
    return 0


def cmd_field(args) -> int:
    meta = load_metadata()
    if not meta.is_field(args.name):
        print(json.dumps({"error": "unknown_field", "field": args.name}, indent=2))
        return 1
    print(json.dumps(meta.field(args.name), indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="idscore", description="SILO Score — PUMF re-identification risk")
    sub = p.add_subparsers(dest="command", required=True)

    q = sub.add_parser("query", help="score a query (JSON string, @file, or - for stdin)")
    q.add_argument("payload")
    q.set_defaults(func=cmd_query)

    d = sub.add_parser("docs", help="print field documentation (most→least identifying)")
    d.add_argument("--n", type=int, default=None, help="return top N fields (default all)")
    d.add_argument("--format", choices=["json", "markdown"], default="json")
    d.set_defaults(func=cmd_docs)

    fs = sub.add_parser("fields", help="list all queryable fields by importance")
    fs.set_defaults(func=cmd_fields)

    fld = sub.add_parser("field", help="show one field's codes")
    fld.add_argument("name")
    fld.set_defaults(func=cmd_field)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
