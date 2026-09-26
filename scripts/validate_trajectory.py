"""Validate one trajectory or the whole inbox without modifying files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import ROOT, validate_inbox, validate_trajectory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, help="one trajectory JSON path")
    args = parser.parse_args()
    try:
        if args.path:
            item = validate_trajectory(args.path)
            print(f"valid: {item['id']}")
        else:
            entries = validate_inbox(ROOT)
            print(f"valid inbox: {len(entries)} trajectory(s)")
        return 0
    except (ValueError, OSError) as exc:
        print(f"invalid: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
