"""Fail if Git could publish paths outside Mei's reviewed public scaffold."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PUBLIC_FILES = frozenset(
    {
        ".codex/config.example.toml",
        ".gitignore",
        "README.md",
        "VERSION",
        "docs/architecture/memory-read.architecture.json",
        "docs/architecture/memory-read.html",
        "docs/architecture/memory-read.svg",
        "docs/architecture/memory-promotion.workflow.json",
        "docs/architecture/memory-promotion.html",
        "docs/architecture/memory-promotion.svg",
        "evals/cases/synthetic-task.json",
        "local/paths.example.toml",
        "policies/memory-policy.md",
        "policies/permissions-policy.md",
        "policies/promotion-policy.md",
        "policies/skill-policy.md",
        "policies/trajectory-policy.md",
        "registry/memory.example.yaml",
        "registry/projects.example.yaml",
        "registry/skills.example.yaml",
        "requirements.txt",
        "schemas/memory.schema.json",
        "schemas/skill.schema.json",
        "schemas/trajectory.schema.json",
        "schemas/continuity-checkpointer.schema.json",
        "schemas/continuity-index.schema.json",
        "schemas/continuity-project.schema.json",
        "scripts/_common.py",
        "scripts/audit_skill.py",
        "scripts/archive_trajectory.py",
        "scripts/check_public_git.py",
        "scripts/check_continuity.py",
        "scripts/checkpoint_continuity.py",
        "scripts/continuity_adapters.py",
        "scripts/sync_continuity.py",
        "scripts/complete_continuity.py",
        "scripts/init_local.py",
        "scripts/normalize_trajectory.py",
        "scripts/rebuild_registry.py",
        "scripts/validate_repository.py",
        "scripts/validate_trajectory.py",
        "tests/test_v0.py",
        "tests/test_continuity.py",
        "tests/test_checkpoint_continuity.py",
        "tests/test_adapters.py",
        "tests/test_sync_continuity.py",
        "tests/test_complete_continuity.py",
    }
)
SKELETON_PATH = re.compile(r"^(?:memory|skills|trajectories|evals|logs)/(?:[^/]+/)*\.gitkeep$")


def public_path(path: str) -> bool:
    return path in PUBLIC_FILES or SKELETON_PATH.fullmatch(path) is not None


def git_paths(*args: str) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", *args],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return {item.decode("utf-8") for item in result.stdout.split(b"\0") if item}


def main() -> int:
    try:
        tracked = git_paths("--cached")
        visible_untracked = git_paths("--others", "--exclude-standard")
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"public Git check failed: {exc}", file=sys.stderr)
        return 1

    blocked = sorted(path for path in tracked | visible_untracked if not public_path(path))
    nonempty_placeholders = sorted(
        path
        for path in tracked | visible_untracked
        if SKELETON_PATH.fullmatch(path) and (ROOT / path).is_file() and (ROOT / path).stat().st_size
    )
    if blocked or nonempty_placeholders:
        if blocked:
            print("Paths outside public scaffold:", *blocked, sep="\n- ", file=sys.stderr)
        if nonempty_placeholders:
            print("Nonempty folder placeholders:", *nonempty_placeholders, sep="\n- ", file=sys.stderr)
        return 1

    print(f"public Git paths valid: {len(tracked)} tracked, {len(visible_untracked)} visible untracked")
    print("Review staged content manually; path checks cannot detect private text inside allowed files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
