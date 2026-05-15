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
import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


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
    parser.add_argument("--command", required=True, help="Remote command to execute")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        remote_cmd = build_args(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.print_command or args.dry_run:
        print(" ".join(shlex.quote(part) for part in mask_args(remote_cmd)))

    if args.dry_run:
        return 0

    env = os.environ.copy()
    if args.password_env and args.backend != "plink":
        env["SSHPASS"] = env.get(args.password_env, "")

    completed = subprocess.run(
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
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
