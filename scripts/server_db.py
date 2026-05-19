#!/usr/bin/env python3
"""
Lightweight JSON store for saved server connection definitions.

Data is stored at ~/.remote-ops/servers.json — outside the repo so server
details (hostnames, IPs, key paths) are not accidentally committed.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path.home() / ".remote-ops"
DATA_FILE = DATA_DIR / "servers.json"

SCHEMA_FIELDS = (
    "name", "host", "session", "user", "port", "key",
    "hostkey", "backend", "shell", "notes", "updated_at",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ServerDB:
    def __init__(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict] = self._load()

    def _load(self) -> dict[str, dict]:
        if not DATA_FILE.is_file():
            return {}
        try:
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(
                f"WARNING: failed to load {DATA_FILE} — {exc}. "
                "Starting with empty server list.",
                file=sys.stderr,
            )
            return {}
        if not isinstance(raw, dict):
            return {}
        return raw

    def _write(self) -> None:
        DATA_FILE.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def get(self, name: str) -> dict | None:
        return self._data.get(name)

    def list_all(self) -> dict[str, dict]:
        return dict(self._data)

    def names(self) -> list[str]:
        return sorted(self._data.keys())

    def save(
        self,
        name: str,
        *,
        host: str | None = None,
        session: str | None = None,
        user: str | None = None,
        port: int = 22,
        key: str | None = None,
        hostkey: str | None = None,
        backend: str | None = None,
        shell: str = "bash",
        notes: str | None = None,
    ) -> dict:
        entry: dict = {
            "name": name,
            "host": host,
            "session": session,
            "user": user,
            "port": port,
            "key": key,
            "hostkey": hostkey,
            "backend": backend,
            "shell": shell,
            "notes": notes,
            "updated_at": _now_iso(),
        }
        self._data[name] = entry
        self._write()
        return dict(entry)

    def delete(self, name: str) -> bool:
        if name not in self._data:
            return False
        del self._data[name]
        self._write()
        return True
