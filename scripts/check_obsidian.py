"""Read-only validation for a configured Mei Obsidian namespace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_obsidian import check_vault


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--namespace")
    parser.add_argument("--memory-root", type=Path)
    args = parser.parse_args()
    result = check_vault(args.vault, namespace=args.namespace, memory_root=args.memory_root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
