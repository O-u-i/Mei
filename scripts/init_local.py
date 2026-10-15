"""Create missing, Git-ignored files and the local Obsidian vault for one Mei installation."""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write_if_missing(path: Path, content: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    except FileExistsError:
        return False
    return True


def initialize(root: Path = ROOT, codex_home: Path | None = None) -> list[Path]:
    root = root.resolve()
    codex_home = (codex_home or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))).resolve()
    codex_home_tui = Path(os.environ.get("MEI_CODEX_HOME_TUI", str(codex_home))).resolve()
    paths = {
        "mei_root": root,
        "memory_root": root / "memory" / "markdown",
        "codex_home": codex_home,
        "codex_home_gui": codex_home,
        "codex_home_tui": codex_home_tui,
        "codex_memory_source_gui": codex_home / "memories",
        "codex_memory_source_tui": codex_home_tui / "memories",
        "skills_root": codex_home / "skills",
        "inbox": root / "trajectories" / "inbox",
    }
    obsidian_vault = Path(
        os.environ.get("MEI_OBSIDIAN_VAULT", str(root / "memory" / "markdown"))
    ).expanduser().resolve()
    obsidian_vault.mkdir(parents=True, exist_ok=True)
    (obsidian_vault / ".obsidian").mkdir(exist_ok=True)
    for category in ("profile", "preferences", "domain", "procedural", "project", "feedback"):
        (obsidian_vault / category).mkdir(exist_ok=True)
    (obsidian_vault / "MEMORY.md").touch(exist_ok=True)

    local_config = "[paths]\n"
    local_config += "".join(f"{key} = {json.dumps(str(value), ensure_ascii=False)}\n" for key, value in paths.items())
    local_config += f"obsidian_vault = {json.dumps(str(obsidian_vault), ensure_ascii=False)}\n"
    local_config += f"obsidian_memory_dir = {json.dumps(os.environ.get('MEI_OBSIDIAN_MEMORY_DIR', ''), ensure_ascii=False)}\n"
    local_config += '\n[profiles]\nproject = "mei-project"\nmaintainer = "mei-maintainer"\n'

    sources = [
        (root / "local" / "paths.toml", local_config),
        (root / ".codex" / "config.toml", (root / ".codex" / "config.example.toml").read_text(encoding="utf-8")),
    ]
    for name in ("memory", "projects", "skills"):
        template = root / "registry" / f"{name}.example.yaml"
        sources.append((root / "registry" / f"{name}.yaml", template.read_text(encoding="utf-8")))
    return [target for target, content in sources if write_if_missing(target, content)]


def main() -> None:
    created = initialize()
    if created:
        for path in created:
            print(f"created: {path.relative_to(ROOT)}")
    else:
        print("local files already present; no changes")


if __name__ == "__main__":
    main()
