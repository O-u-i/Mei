"""Read-only validation for a configured Mei Obsidian namespace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_obsidian import check_vault
from review_proposals import check_review_workbench


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", type=Path)
    parser.add_argument("--namespace")
    parser.add_argument("--memory-root", type=Path)
    parser.add_argument("--mei-root", type=Path)
    args = parser.parse_args()
    result = check_vault(args.vault, namespace=args.namespace, memory_root=args.memory_root)
    workbench = check_review_workbench(mei_root=args.mei_root, vault_root=args.vault, namespace=args.namespace)
    result["review_workbench"] = workbench
    if workbench.get("status") != "ok":
        result["status"] = "invalid"
        result.setdefault("errors", []).extend(workbench.get("errors", []))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
