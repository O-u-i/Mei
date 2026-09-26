"""Create missing, Git-ignored files for one Mei installation without overwriting data."""

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
    paths = {
        "mei_root": root,
        "codex_home": codex_home,
        "skills_root": codex_home / "skills",
        "inbox": root / "trajectories" / "inbox",
    }
    local_config = "[paths]\n"
    local_config += "".join(f"{key} = {json.dumps(str(value), ensure_ascii=False)}\n" for key, value in paths.items())
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
