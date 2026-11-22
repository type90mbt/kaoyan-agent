"""写入前快照：AI 修改或删除文件之前，先把原文件复制一份，可以一键撤销。

快照保存在 `~/.kaoyan/snapshots/<快照id>/`，`manifest.json` 记录每个文件原来是否存在。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any

from ..db import new_id

KEEP = 200


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def take(self, paths: list[Path], reason: str) -> str:
        sid = f"{int(time.time() * 1000)}-{new_id()[:8]}"
        folder = self.root / sid
        folder.mkdir(parents=True)
        entries = []
        for i, p in enumerate(paths):
            entry: dict[str, Any] = {"path": str(p), "existed": p.exists(), "is_dir": p.is_dir()}
            if p.is_file():
                backup = folder / f"{i}{p.suffix}"
                shutil.copy2(p, backup)
                entry["backup"] = backup.name
            elif p.is_dir():
                backup = folder / f"{i}_dir"
                shutil.copytree(p, backup)
                entry["backup"] = backup.name
            entries.append(entry)
        (folder / "manifest.json").write_text(
            json.dumps({"id": sid, "reason": reason, "created_at": time.time(), "entries": entries}, ensure_ascii=False),
            encoding="utf-8",
        )
        self._prune()
        return sid

    def list(self) -> list[dict[str, Any]]:
        items = []
        for folder in sorted(self.root.iterdir(), reverse=True):
            manifest = folder / "manifest.json"
            if manifest.exists():
                data = json.loads(manifest.read_text(encoding="utf-8"))
                data["undone"] = (folder / "UNDONE").exists()
                items.append(data)
        return items

    def undo(self, sid: str) -> list[str]:
        folder = (self.root / sid).resolve()
        if not folder.is_relative_to(self.root.resolve()) or not (folder / "manifest.json").exists():
            raise FileNotFoundError(f"找不到快照 {sid}")
        data = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        restored = []
        for entry in reversed(data["entries"]):
            target = Path(entry["path"])
            if target.is_dir() and not entry.get("is_dir"):
                shutil.rmtree(target)
            elif target.exists():
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
            if entry["existed"] and entry.get("backup"):
                src = folder / entry["backup"]
                target.parent.mkdir(parents=True, exist_ok=True)
                if src.is_dir():
                    shutil.copytree(src, target)
                else:
                    shutil.copy2(src, target)
            restored.append(str(target))
        (folder / "UNDONE").write_text("1", encoding="utf-8")
        return restored

    def _prune(self) -> None:
        folders = sorted(p for p in self.root.iterdir() if p.is_dir())
        for old in folders[:-KEEP]:
            shutil.rmtree(old, ignore_errors=True)
