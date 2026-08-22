"""Build, validate, and inspect the local Qdrant filing index."""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from retrieval import (  # noqa: E402
    rebuild_filing_index,
    search_filing,
    validate_filing_index,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("rebuild")
    subparsers.add_parser("validate")
    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("company")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=5)
    return parser.parse_args()


def main() -> None:
    """Run the requested filing-index command."""
    args = _parse_args()
    if args.command in (None, "rebuild"):
        result = rebuild_filing_index()
    elif args.command == "validate":
        result = validate_filing_index()
    elif args.command == "search":
        result = [
            {
                "evidence_id": item.evidence_id,
                "score": item.score,
                "text": item.text,
                "metadata": item.metadata,
            }
            for item in search_filing(args.query, args.company, args.limit)
        ]
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
