"""Create missing local files and the direct canonical vault; never register an app vault."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from memory_markdown import initialize_memory

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
    codex_home_gui1 = Path(os.environ.get("MEI_CODEX_HOME_GUI1", str(codex_home))).resolve()
    codex_home_gui2 = Path(os.environ.get("MEI_CODEX_HOME_GUI2", str(codex_home))).resolve()
    paths = {
        "mei_root": root,
        "memory_root": root / "memory" / "markdown",
        "codex_home": codex_home,
        "codex_home_gui1": codex_home_gui1,
        "codex_home_gui2": codex_home_gui2,
        "codex_memory_source_gui1": codex_home_gui1 / "memories",
        "codex_memory_source_gui2": codex_home_gui2 / "memories",
        "skills_root": Path.home() / ".agents" / "skills",
        "inbox": root / "trajectories" / "inbox",
    }
    obsidian_vault = Path(
        os.environ.get("MEI_OBSIDIAN_VAULT", str(root / "memory" / "markdown"))
    ).expanduser().resolve()
    if obsidian_vault != paths["memory_root"] or os.environ.get("MEI_OBSIDIAN_MEMORY_DIR", ""):
        raise ValueError("Mei v2 initialization requires the memory/markdown direct-root vault and an empty namespace")
    initialize_memory(paths["memory_root"])
    (obsidian_vault / ".obsidian").mkdir(exist_ok=True)

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


def main() -> int:
    try:
        created = initialize()
    except (OSError, ValueError) as exc:
        print(f"initialization failed: {exc}", file=sys.stderr)
        return 1
    if created:
        for path in created:
            print(f"created: {path.relative_to(ROOT)}")
    else:
        print("local files already present; no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
