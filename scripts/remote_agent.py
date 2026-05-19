#!/usr/bin/env python3
"""Remote Ops Agent — structured remote execution via JSONL over stdin/stdout.

Deployed to /tmp/remote-ops-agent.py by invoke_remote.py on first use.
Single request-response cycle per invocation. No persistent process, no open ports.

Protocol: reads one JSON line from stdin, writes one JSON line to stdout.
Request:  {"method": "...", "id": "...", "params": {...}}
Response: {"ok": true, "data": {...}} or {"ok": false, "error": "..."}
"""

from __future__ import annotations

import base64
import glob
import hashlib
import json
import os
import platform
import socket
import stat
import subprocess
import sys
from pathlib import Path


def _response(ok: bool, data: object = None, error: str = "", req_id: str = "") -> None:
    resp: dict = {"ok": ok}
    if ok:
        resp["data"] = data if data is not None else {}
    else:
        resp["error"] = error
    if req_id:
        resp["id"] = req_id
    print(json.dumps(resp, ensure_ascii=False), flush=True)


def _fail_and_exit(error: str, req_id: str = "") -> None:
    _response(ok=False, error=error, req_id=req_id)
    raise SystemExit(1)


def _guard_python3() -> None:
    if sys.version_info < (3, 7):
        _response(
            ok=False,
            error=f"Python >= 3.7 required, found {sys.version_info.major}.{sys.version_info.minor}",
        )
        raise SystemExit(1)


def _method_ping(params: dict) -> dict:
    return {
        "hostname": socket.gethostname(),
        "python": sys.version,
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "uid": os.getuid() if hasattr(os, "getuid") else None,
    }


def _method_exec(params: dict) -> dict:
    command: str = params["command"]
    shell: str = params.get("shell", "bash")
    cwd: str | None = params.get("cwd")
    timeout: int = int(params.get("timeout", 60))
    env_add: dict | None = params.get("env")

    env = os.environ.copy()
    if env_add:
        env.update({str(k): str(v) for k, v in env_add.items()})

    try:
        completed = subprocess.run(
            command,
            shell=True,
            executable=f"/bin/{shell}" if shell in ("bash", "sh") else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Command timed out after {timeout}s"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "returncode": completed.returncode,
    }


def _method_read(params: dict) -> dict:
    path: str = params["path"]
    p = Path(path).expanduser()
    if not p.is_file():
        return {"ok": False, "error": f"Not a file: {path}"}
    try:
        data = p.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "data": base64.b64encode(data).decode("ascii"),
        "size": len(data),
    }


def _method_write(params: dict) -> dict:
    path: str = params["path"]
    data_b64: str = params.get("data", "")
    mode_str: str = params.get("mode", "644")

    p = Path(path).expanduser()
    try:
        data = base64.b64decode(data_b64)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        # Sync to disk so subsequent reads (e.g. checksum verification)
        # see the written data immediately.
        fd: int | None = None
        try:
            fd = os.open(str(p), os.O_RDONLY)
            os.fsync(fd)
        except OSError:
            pass  # fsync is best-effort; filesystem may not support it
        finally:
            if fd is not None:
                os.close(fd)
        mode_bits = int(mode_str, 8)
        os.chmod(str(p), mode_bits)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "path": str(p),
        "size": len(data),
    }


def _method_list(params: dict) -> dict:
    path: str = params["path"]
    pattern: str = params.get("pattern", "*")

    p = Path(path).expanduser()
    if not p.is_dir():
        return {"ok": False, "error": f"Not a directory: {path}"}

    entries: list[dict] = []
    glob_pattern = str(p / pattern)
    for entry_path in sorted(glob.glob(glob_pattern)):
        sp = Path(entry_path)
        try:
            st = sp.stat()
        except OSError:
            continue
        entry_type = "dir" if stat.S_ISDIR(st.st_mode) else "file"
        entries.append({
            "name": sp.name,
            "path": str(sp),
            "type": entry_type,
            "size": st.st_size,
            "mtime": int(st.st_mtime),
        })
    return {"ok": True, "entries": entries}


def _method_checksum(params: dict) -> dict:
    path: str = params["path"]
    p = Path(path).expanduser()
    if not p.exists():
        return {"ok": True, "sha256": None, "size": 0}
    if not p.is_file():
        return {"ok": False, "error": f"Not a file: {path}"}
    try:
        data = p.read_bytes()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {
        "ok": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


DISPATCH = {
    "ping": _method_ping,
    "exec": _method_exec,
    "read": _method_read,
    "write": _method_write,
    "list": _method_list,
    "checksum": _method_checksum,
}


def main() -> None:
    _guard_python3()

    raw = sys.stdin.readline().strip()
    if not raw:
        _fail_and_exit("No input on stdin")

    try:
        req = json.loads(raw)
    except json.JSONDecodeError as exc:
        _fail_and_exit(f"Invalid JSON: {exc}")

    if not isinstance(req, dict):
        _fail_and_exit("Request must be a JSON object")

    method: str = req.get("method", "")
    req_id: str = req.get("id", "")
    params: dict = req.get("params", {})

    if not method:
        _fail_and_exit("Missing 'method' field", req_id)

    handler = DISPATCH.get(method)
    if handler is None:
        _fail_and_exit(f"Unknown method: {method}", req_id)

    try:
        result = handler(params)
    except Exception as exc:
        _fail_and_exit(f"Handler error: {exc}", req_id)

    if result.get("ok") is False:
        _response(ok=False, error=result.get("error", "Unknown error"), req_id=req_id)
    else:
        _response(ok=True, data=result, req_id=req_id)


if __name__ == "__main__":
    main()
