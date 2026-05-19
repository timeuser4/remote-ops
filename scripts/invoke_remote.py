#!/usr/bin/env python3
"""
Run non-interactive remote commands through plink (Windows) or sshpass/ssh (Unix).

Auto-detects the backend based on the current OS but allows explicit override.

Examples:
  python scripts/invoke_remote.py --session my-host --shell bash --command "hostname"
  python scripts/invoke_remote.py --host 192.168.1.50 --user nvidia --key ~/.ssh/id_ed25519 --shell bash --command "pwd"
  python scripts/invoke_remote.py --host 192.168.1.50 --user nvidia --password-env SSHPASS --shell bash --command "df -h"
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from server_db import ServerDB


def detect_backend() -> str:
    return "plink" if platform.system().lower() == "windows" else "sshpass"


def resolve_plink(explicit_path: str | None) -> str:
    if explicit_path:
        candidate = Path(explicit_path).expanduser()
        if candidate.is_file():
            return str(candidate)
        raise FileNotFoundError(f"plink not found at {candidate}")

    found = shutil.which("plink") or shutil.which("plink.exe")
    if found:
        return found

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PuTTY" / "plink.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "PuTTY" / "plink.exe",
        Path(os.environ.get("LocalAppData", r"")) / "Programs" / "PuTTY" / "plink.exe",
        Path(os.environ.get("USERPROFILE", r"")) / "scoop" / "apps" / "putty" / "current" / "plink.exe",
        Path(r"C:\ProgramData\chocolatey\bin\plink.exe"),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)

    raise FileNotFoundError(
        "plink.exe not found in PATH or common PuTTY install locations. "
        "Run scripts/setup.py to install."
    )


def resolve_sshpass() -> str:
    found = shutil.which("sshpass")
    if not found:
        raise FileNotFoundError(
            "sshpass not found. Install via scripts/setup.py or your package manager."
        )
    return found


def build_remote_command(shell_name: str, command: str, cwd: str | None, sudo: bool) -> str:
    if shell_name == "raw":
        if cwd:
            raise ValueError("--cwd is not supported with --shell raw")
        if sudo:
            raise ValueError("--sudo is not supported with --shell raw")
        return command

    inner = command
    if cwd:
        inner = f"cd {shlex.quote(cwd)} && {inner}"
    if sudo:
        inner = f"sudo -n {shell_name} -lc {shlex.quote(inner)}"

    return f"{shell_name} -lc {shlex.quote(inner)}"


def build_plink_args(args: argparse.Namespace) -> list[str]:
    plink = resolve_plink(args.plink)

    if bool(args.session) == bool(args.host):
        raise ValueError("Specify exactly one of --session or --host")

    cmd = [plink, "-batch"]

    if args.hostkey:
        cmd.extend(["-hostkey", args.hostkey])

    if args.key:
        key_path = Path(args.key).expanduser()
        if not key_path.is_file():
            raise FileNotFoundError(f"Key not found: {key_path}")
        cmd.extend(["-i", str(key_path)])

    if args.password_env:
        password = os.environ.get(args.password_env)
        if password is None:
            raise ValueError(f"Environment variable {args.password_env} is not set")
        cmd.extend(["-pw", password])

    if args.session:
        cmd.extend(["-load", args.session])
    else:
        cmd.append("-ssh")
        if args.port and args.port != 22:
            cmd.extend(["-P", str(args.port)])
        target = args.host
        if args.user:
            target = f"{args.user}@{target}"
        cmd.append(target)

    remote_command = build_remote_command(
        shell_name=args.shell, command=args.command, cwd=args.cwd, sudo=args.sudo,
    )
    cmd.append(remote_command)
    return cmd


def build_sshpass_args(args: argparse.Namespace) -> list[str]:
    sshpass = resolve_sshpass()
    cmd = []

    if args.password_env:
        password = os.environ.get(args.password_env)
        if password is None:
            raise ValueError(f"Environment variable {args.password_env} is not set")
        cmd.extend([sshpass, "-e"])

    cmd.append("ssh")
    cmd.extend(["-o", "StrictHostKeyChecking=accept-new"])

    if args.key:
        key_path = Path(args.key).expanduser()
        if not key_path.is_file():
            raise FileNotFoundError(f"Key not found: {key_path}")
        cmd.extend(["-i", str(key_path)])

    if args.port and args.port != 22:
        cmd.extend(["-p", str(args.port)])

    if args.session:
        target = args.session
    else:
        target = args.host
        if args.user:
            target = f"{args.user}@{target}"
    cmd.append(target)

    remote_command = build_remote_command(
        shell_name=args.shell, command=args.command, cwd=args.cwd, sudo=args.sudo,
    )
    cmd.append(remote_command)
    return cmd


def mask_args(argv: list[str]) -> list[str]:
    masked: list[str] = []
    hide_next = False
    for item in argv:
        if hide_next:
            masked.append("******")
            hide_next = False
            continue
        masked.append(item)
        if item in ("-pw",):
            hide_next = True
    return masked


def merge_server_profile(args: argparse.Namespace, saved: dict) -> argparse.Namespace:
    merged: argparse.Namespace = copy.copy(args)

    # host / session: prefer CLI, fall back to saved
    if not merged.host and not merged.session:
        if saved.get("session"):
            merged.session = saved["session"]
        elif saved.get("host"):
            merged.host = saved["host"]

    if not merged.user and saved.get("user"):
        merged.user = saved["user"]

    if merged.port == 22 and saved.get("port") and saved["port"] != 22:
        merged.port = saved["port"]

    if not merged.key and saved.get("key"):
        merged.key = saved["key"]

    if not merged.hostkey and saved.get("hostkey"):
        merged.hostkey = saved["hostkey"]

    if not merged.backend and saved.get("backend"):
        merged.backend = saved["backend"]

    if merged.shell == "bash" and saved.get("shell") and saved["shell"] != "bash":
        merged.shell = saved["shell"]

    return merged


# ---- Remote execution helpers ----

def _run_remote(
    args: argparse.Namespace, command: str, timeout: int = 120, env: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    """Build and run one remote command using the connection params in *args*."""
    args_run: argparse.Namespace = copy.copy(args)
    args_run.command = command
    args_run.print_command = False
    args_run.dry_run = False
    remote_cmd: list[str] = build_args(args_run)
    run_env: dict[str, str] = os.environ.copy()
    if args.password_env and args.backend != "plink":
        run_env["SSHPASS"] = run_env.get(args.password_env, "")
    if env:
        run_env.update(env)
    return subprocess.run(
        remote_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=run_env,
        timeout=timeout,
    )


def _encode_base64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _detect_remote_python(args: argparse.Namespace) -> str:
    """Detect the available Python 3 interpreter on the remote host.

    Returns 'python3' or 'python' whichever passes the version check.
    Raises SystemExit if neither is available.
    """
    for candidate in ("python3", "python"):
        result: subprocess.CompletedProcess[str] = _run_remote(
            args,
            f"{candidate} -c 'import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)'",
            timeout=30,
        )
        if result.returncode == 0:
            return candidate
    # Neither worked — run python3 one more time to capture the error output
    result = _run_remote(
        args,
        "python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3,7) else 1)'",
        timeout=30,
    )
    print(
        "ERROR: remote host does not have Python >= 3.7 (tried python3 and python).\n"
        f"  returncode={result.returncode}\n"
        f"  stderr: {result.stderr.strip()}",
        file=sys.stderr,
    )
    raise SystemExit(1)


def _remote_agent_request_payload(method: str, params: dict, req_id: str = "1") -> str:
    """Build a JSON request for the remote agent, encoded as a one-line remote command."""
    req: dict = {"method": method, "id": req_id, "params": params}
    return json.dumps(req, ensure_ascii=False)


def _agent_remote_command(
    agent_path: str, request_json: str, args: argparse.Namespace,
) -> str:
    """Build the remote command string that sends a request to the agent.

    Uses the detected remote Python interpreter for base64 decoding
    (avoids BSD/GNU base64 -d/-D incompatibility).
    """
    python_exe: str = getattr(args, "_remote_python", "python3")
    b64: str = _encode_base64(request_json)
    decoder: str = (
        f"{python_exe} -c 'import base64,sys;"
        f"sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))'"
    )
    return (
        f"printf '%s' '{b64}' | {decoder} | {python_exe} {shlex.quote(agent_path)}"
    )


def _ensure_agent(args: argparse.Namespace, agent_path: str) -> None:
    """Check whether the remote agent exists; deploy it if not.

    Detects the available Python interpreter (python3 or python) and stores
    it as ``args._remote_python``.  Raises SystemExit if Python >= 3.7 is
    unavailable or deployment fails.
    """
    # Detect and store the remote Python interpreter
    python_exe: str = _detect_remote_python(args)
    args._remote_python = python_exe  # type: ignore[attr-defined]

    # Check whether agent file already exists
    result: subprocess.CompletedProcess[str] = _run_remote(
        args, f"test -f {shlex.quote(agent_path)}", timeout=30,
    )
    if result.returncode == 0:
        return  # Already deployed

    # Deploy agent — read local script, encode, write to remote
    local_agent: Path = Path(__file__).resolve().parent / "remote_agent.py"
    if not local_agent.is_file():
        print(
            f"ERROR: local agent script not found at {local_agent}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    agent_source: str = local_agent.read_text(encoding="utf-8")
    agent_b64: str = _encode_base64(agent_source)
    decoder: str = (
        f"{python_exe} -c 'import base64,sys;"
        f"sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))'"
    )
    deploy_command: str = (
        f"printf '%s' '{agent_b64}' | {decoder} > {shlex.quote(agent_path)}"
        f" && chmod +x {shlex.quote(agent_path)}"
    )

    result = _run_remote(args, deploy_command, timeout=30)
    if result.returncode != 0:
        print(
            f"ERROR: failed to deploy agent to {agent_path}\n"
            f"  stderr: {result.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Verify deployment
    result = _run_remote(
        args,
        f"printf '%s' '{{\"method\":\"ping\"}}' | {python_exe} {shlex.quote(agent_path)}",
        timeout=30,
    )
    if result.returncode != 0:
        print(
            f"ERROR: agent deployed but verification failed\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}",
            file=sys.stderr,
        )
        raise SystemExit(1)

    print(f"Agent deployed to {agent_path} on remote host.", file=sys.stderr)


def build_args(args: argparse.Namespace) -> list[str]:
    backend = args.backend or detect_backend()
    if backend == "plink":
        return build_plink_args(args)
    elif backend == "sshpass":
        return build_sshpass_args(args)
    else:
        raise ValueError(f"Unknown backend: {backend}. Use 'plink' or 'sshpass'.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a non-interactive remote command through plink or sshpass/ssh.",
    )
    parser.add_argument(
        "--backend",
        choices=("plink", "sshpass"),
        default=None,
        help="Force a specific backend. Auto-detected from OS if omitted.",
    )
    parser.add_argument("--plink", help="Explicit path to plink.exe (plink backend only)")
    parser.add_argument("--session", help="Saved session name (PuTTY) or SSH config Host entry")
    parser.add_argument("--host", help="SSH host or IP address")
    parser.add_argument("--user", help="SSH username")
    parser.add_argument("--port", type=int, default=22, help="SSH port")
    parser.add_argument("--key", help="Path to SSH key (.ppk for plink, standard key for sshpass)")
    parser.add_argument("--hostkey", help="Pinned SSH host key fingerprint (plink backend only)")
    parser.add_argument(
        "--password-env",
        help="Environment variable name that holds the SSH password",
    )
    parser.add_argument(
        "--shell",
        choices=("bash", "sh", "raw"),
        default="bash",
        help="Remote shell wrapper",
    )
    parser.add_argument("--cwd", help="Remote working directory for bash/sh mode")
    parser.add_argument("--sudo", action="store_true", help="Wrap remote command with sudo -n")
    parser.add_argument(
        "--print-command", action="store_true", help="Print the local command before execution"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print command and exit without executing"
    )
    parser.add_argument("--command", help="Remote command to execute")
    parser.add_argument("--server", help="Name of a saved server to load connection parameters from")
    parser.add_argument("--save", help="Save connection parameters under this name after a successful connection")
    parser.add_argument("--delete-server", help="Delete a saved server definition and exit")
    parser.add_argument("--list-servers", action="store_true", help="List all saved servers and exit")
    parser.add_argument("--notes", help="Notes to attach when saving a server (only meaningful with --save)")
    parser.add_argument(
        "--base64", action="store_true",
        help="Auto-encode --command via base64 to avoid shell escaping issues",
    )
    parser.add_argument(
        "--agent", action="store_true",
        help="Execute via remote-ops-agent on the remote host (auto-deploys if absent)",
    )
    parser.add_argument(
        "--agent-path",
        default="/tmp/remote-ops-agent.py",
        help="Path to the remote agent script (default: /tmp/remote-ops-agent.py)",
    )
    parser.add_argument(
        "--sync",
        help="Sync a local file to the remote host. Format: LOCAL_PATH:REMOTE_PATH",
    )
    return parser.parse_args()


def _call_agent(
    args: argparse.Namespace, agent_path: str, method: str,
    params: dict, req_id: str = "1", timeout: int = 30,
) -> tuple[dict, subprocess.CompletedProcess[str]]:
    """Call a remote agent method. Returns (parsed_response, completed_process)."""
    request_json: str = _remote_agent_request_payload(method, params, req_id)
    remote_command: str = _agent_remote_command(agent_path, request_json, args)
    result: subprocess.CompletedProcess[str] = _run_remote(args, remote_command, timeout=timeout)
    try:
        response: dict = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        response = {"ok": False, "error": f"Invalid agent response: {result.stdout.strip()}"}
    return response, result


def _main_sync(args: argparse.Namespace, db: ServerDB) -> int:
    """Sync a local file to the remote host via the agent."""
    agent_path: str = args.agent_path
    parts: list[str] = args.sync.split(":")
    # Handle Windows drive letters (e.g. C:\Users\file.txt:/remote/path)
    if len(parts) >= 3 and len(parts[0]) == 1 and parts[0].isalpha():
        local_raw = parts[0] + ":" + parts[1]
        remote_raw = parts[2]
    elif len(parts) >= 2:
        local_raw = parts[0]
        remote_raw = parts[1]
    else:
        print("ERROR: --sync format is LOCAL_PATH:REMOTE_PATH", file=sys.stderr)
        return 2
    local_path: Path = Path(local_raw).expanduser().resolve()
    if not local_path.is_file():
        print(f"ERROR: local file not found: {local_path}", file=sys.stderr)
        return 1

    # Local checksum
    local_data: bytes = local_path.read_bytes()
    local_sha256: str = hashlib.sha256(local_data).hexdigest()
    local_b64: str = base64.b64encode(local_data).decode("ascii")

    if args.dry_run:
        print(
            f"[dry-run] Would sync {local_path} ({len(local_data)} bytes) "
            f"-> {remote_raw}",
            file=sys.stderr,
        )
        return 0

    _ensure_agent(args, agent_path)

    # Get remote checksum
    resp, _ = _call_agent(args, agent_path, "checksum", {"path": remote_raw}, "ck1")
    if not resp.get("ok"):
        print(f"ERROR: agent checksum failed: {resp.get('error')}", file=sys.stderr)
        return 1

    data_raw: object = resp.get("data")
    if not isinstance(data_raw, dict):
        print(
            f"ERROR: unexpected checksum response structure for {remote_raw}",
            file=sys.stderr,
        )
        return 1
    data: dict = data_raw
    if data.get("ok"):
        remote_sha256_raw: object = data.get("sha256")
        # sha256 must be a non-empty hex string (or None for non-existent files)
        remote_sha256: str | None = (
            remote_sha256_raw
            if isinstance(remote_sha256_raw, str) and len(remote_sha256_raw) == 64
            else None
        )
        if remote_sha256 == local_sha256:
            print(f"Already in sync: {remote_raw}")
            return 0
        if remote_sha256 is not None:
            print(
                f"Changed: {local_path} ({len(local_data)} bytes) "
                f"-> {remote_raw}",
                file=sys.stderr,
            )

    # Push
    resp, _ = _call_agent(args, agent_path, "write", {"path": remote_raw, "data": local_b64}, "wr1")
    if not resp.get("ok"):
        print(f"ERROR: write failed: {resp.get('error')}", file=sys.stderr)
        return 1

    write_data: dict = resp.get("data", {})
    if not write_data.get("ok"):
        print(f"ERROR: write failed: {write_data.get('error')}", file=sys.stderr)
        return 1

    # Verify: re-check remote checksum to confirm the write took effect.
    resp, _ = _call_agent(
        args, agent_path, "checksum", {"path": remote_raw}, "verify",
    )
    if resp.get("ok"):
        verify_data: dict = resp.get("data", {})
        if isinstance(verify_data, dict) and verify_data.get("ok"):
            verify_sha256: str | None = verify_data.get("sha256")
            if verify_sha256 != local_sha256:
                print(
                    f"ERROR: write verification failed — checksum mismatch "
                    f"after writing to {remote_raw}",
                    file=sys.stderr,
                )
                return 1

    print(f"Synced: {local_path} -> {remote_raw}  ({len(local_data)} bytes)")
    return 0


def _make_agent_request(args: argparse.Namespace) -> str:
    """Build a JSON agent request from --command, auto-wrapping exec calls."""
    try:
        req: dict = json.loads(args.command)
        if isinstance(req, dict) and "method" in req:
            method: str = req.get("method", "exec")
            params: dict = req.get("params", {})
            req_id: str = req.get("id", "1")
            return _remote_agent_request_payload(method, params, req_id)
    except (json.JSONDecodeError, ValueError):
        pass
    return _remote_agent_request_payload(
        "exec", {"command": args.command, "shell": args.shell}, "1",
    )


def _main_agent(args: argparse.Namespace, db: ServerDB) -> int:
    """Execute --command through the remote agent, deploying it first if needed."""
    agent_path: str = args.agent_path

    # Dry-run: skip agent deployment and connection check
    if args.dry_run:
        request_json: str = _make_agent_request(args)
        remote_command_str: str = _agent_remote_command(agent_path, request_json, args)
        args_agent: argparse.Namespace = copy.copy(args)
        args_agent.command = remote_command_str
        args_agent.agent = False
        try:
            remote_cmd: list[str] = build_args(args_agent)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(" ".join(shlex.quote(part) for part in mask_args(remote_cmd)))
        return 0

    # Ensure agent is available on the remote host
    _ensure_agent(args, agent_path)

    # Build the agent request and remote command
    request_json: str = _make_agent_request(args)

    # Build the remote command that pipes to the agent
    remote_command_str: str = _agent_remote_command(agent_path, request_json, args)
    args_agent: argparse.Namespace = copy.copy(args)
    args_agent.command = remote_command_str
    args_agent.agent = False

    try:
        remote_cmd: list[str] = build_args(args_agent)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.print_command or args.dry_run:
        print(" ".join(shlex.quote(part) for part in mask_args(remote_cmd)))

    if args.dry_run:
        return 0

    env: dict[str, str] = os.environ.copy()
    if args.password_env and args.backend != "plink":
        env["SSHPASS"] = env.get(args.password_env, "")

    completed: subprocess.CompletedProcess[str] = subprocess.run(
        remote_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    # Parse agent response from stdout
    exit_code: int = 0
    response_text: str = completed.stdout.strip() if completed.stdout else ""
    try:
        agent_response: dict = json.loads(response_text)
    except json.JSONDecodeError:
        # Non-JSON output — print raw
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)
        exit_code = completed.returncode

    else:
        if agent_response.get("ok"):
            data: dict = agent_response.get("data", {})
            if "stdout" in data or "stderr" in data:
                if data.get("stdout"):
                    print(data["stdout"], end="")
                if data.get("stderr"):
                    print(data["stderr"], end="", file=sys.stderr)
                exit_code = data.get("returncode", 0)
            else:
                print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            error: str = agent_response.get("error", "Unknown agent error")
            print(f"AGENT ERROR: {error}", file=sys.stderr)
            exit_code = 1

    # Auto-save on successful connection
    if args.save and exit_code == 0:
        db.save(
            name=args.save,
            host=args.host,
            session=args.session,
            user=args.user,
            port=args.port,
            key=args.key,
            hostkey=args.hostkey,
            backend=args.backend or detect_backend(),
            shell=args.shell,
            notes=args.notes,
        )
        print(f"Server saved as '{args.save}'.", file=sys.stderr)

    return exit_code


def main() -> int:
    args = parse_args()
    db: ServerDB = ServerDB()

    # Immediate actions — no command needed
    if args.list_servers:
        entries: dict[str, dict] = db.list_all()
        if not entries:
            print("No saved servers.", file=sys.stderr)
            return 0
        max_len: int = max(len(n) for n in entries)
        for name in db.names():
            entry: dict = entries[name]
            host_or_session: str = entry.get("session") or entry.get("host") or "-"
            user_part: str = entry.get("user") or ""
            target: str = f"{user_part}@{host_or_session}" if user_part else host_or_session
            notes: str = entry.get("notes") or ""
            line: str = f"  {name:<{max_len}}  {target}"
            if notes:
                line += f"  # {notes}"
            print(line)
        return 0

    if args.delete_server:
        if db.delete(args.delete_server):
            print(f"Server '{args.delete_server}' deleted.")
            return 0
        print(f"Server '{args.delete_server}' not found.", file=sys.stderr)
        return 1

    if args.base64 and args.agent:
        print("ERROR: --base64 and --agent are mutually exclusive.", file=sys.stderr)
        return 2

    # Load saved server profile if --server was given
    if args.server:
        saved: dict | None = db.get(args.server)
        if not saved:
            print(
                f"ERROR: saved server '{args.server}' not found. "
                f"Use --list-servers to see available servers.",
                file=sys.stderr,
            )
            return 2
        args = merge_server_profile(args, saved)

    # Sync mode (uses agent internally, no --command needed)
    if args.sync:
        return _main_sync(args, db)

    # Guard for interactive operations
    if not args.command:
        print(
            "ERROR: --command is required for remote execution. "
            "Use --list-servers or --delete-server for server management.",
            file=sys.stderr,
        )
        return 2

    # Agent mode
    if args.agent:
        return _main_agent(args, db)

    # Base64 mode: wrap command in decode+exec pattern.
    # Detect the remote Python interpreter first (one SSH round trip) and use
    # ONLY Python for decoding, avoiding BSD/GNU base64 -d/-D incompatibility.
    # This mirrors the approach `_agent_remote_command` already uses.
    if args.base64:
        b64: str = _encode_base64(args.command)
        args = copy.copy(args)
        # For --print-command or --dry-run, use python3 as default without SSH.
        if args.print_command or args.dry_run:
            python_exe: str = "python3"
        else:
            python_exe = _detect_remote_python(args)
        decoder: str = (
            f"{python_exe} -c 'import base64,sys; "
            f"sys.stdout.buffer.write(base64.b64decode(sys.stdin.buffer.read()))'"
        )
        args.command = (
            f"printf '%s' '{b64}' | {decoder} | {args.shell}"
        )

    try:
        remote_cmd: list[str] = build_args(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.print_command or args.dry_run:
        print(" ".join(shlex.quote(part) for part in mask_args(remote_cmd)))

    if args.dry_run:
        return 0

    env: dict[str, str] = os.environ.copy()
    if args.password_env and args.backend != "plink":
        env["SSHPASS"] = env.get(args.password_env, "")

    completed: subprocess.CompletedProcess[str] = subprocess.run(
        remote_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if completed.stdout:
        print(completed.stdout, end="")
    if completed.stderr:
        print(completed.stderr, end="", file=sys.stderr)

    # Auto-save on successful connection
    if args.save and completed.returncode == 0:
        db.save(
            name=args.save,
            host=args.host,
            session=args.session,
            user=args.user,
            port=args.port,
            key=args.key,
            hostkey=args.hostkey,
            backend=args.backend or detect_backend(),
            shell=args.shell,
            notes=args.notes,
        )
        print(f"Server saved as '{args.save}'.", file=sys.stderr)

    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
