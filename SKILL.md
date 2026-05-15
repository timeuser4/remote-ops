---
name: remote-ops
description: Use when the user wants to connect from the local machine to a specified remote host and perform remote inspection or administration with non-interactive commands. Supports plink on Windows and sshpass/ssh on macOS/Linux. Covers saved sessions, explicit SSH targets, key or agent auth, host key pinning, and repeatable remote command execution.
---

# Remote Ops

## Overview

Use this skill when work must be performed on a remote host from the local machine. It is optimized for one-shot remote commands and repeatable remote workflows, not long-lived interactive terminal sessions.

- **Windows**: uses `plink.exe` (PuTTY Link)
- **macOS / Linux**: uses `sshpass` + `ssh`

## When To Use

- The user explicitly asks to use remote-ops, plink, or sshpass.
- The task must run on a remote Linux or Unix host reachable by SSH from this machine.
- The remote target is provided as a saved session or as `host`, `user`, `port`, and auth details.
- You need repeatable remote inspection, deployment, log collection, service management, or command execution.

## Preconditions

- Run `python scripts/setup.py` once per machine to install the required tools.
- Windows: installs the correct PuTTY .msi for the detected architecture (x64/arm64/x86).
- macOS: installs sshpass via Homebrew.
- Linux: installs sshpass via the detected package manager (apt/dnf/yum/pacman/zypper).
- Prefer key-based auth over inline passwords.
- If password auth is unavoidable, set a temporary environment variable such as `SSHPASS` or `PLINK_PASSWORD` and use `--password-env`.
- Ask for missing target details when they cannot be inferred safely:
  - saved session name, or host and username
  - port if not `22`
  - remote shell type, default `bash`
  - key path or other auth method
  - optional host key fingerprint for first-time connections (plink backend)

## Workflow

1. Confirm the target and auth mode.
2. Prefer `scripts/invoke_remote.py` over hand-built commands — it handles quoting, saved sessions, key auth, host key pinning, and non-interactive mode for both backends.
3. Smoke-test the connection with a narrow command such as `hostname && uname -a && pwd`.
4. Run read-only inspection as small, explicit remote commands.
5. File size strategy for edits:
   - Large files: download to local workspace first, edit locally, then upload back to remote.
   - Small files: direct remote edits are acceptable.
   - Default heuristic: treat files over ~200 lines or ~8 KB as large unless the user specifies otherwise.
6. For any multi-line script, complex quoting, heredoc, JSON/YAML generation, or remote file edits, do not pass the script body directly as `--command`. Encode the script as base64 locally and run a short remote decoder command instead.
7. For remote writes, create backups first, write idempotently, and verify the resulting file contents with a second read-only command.
8. Report the exact remote command intent, the output, and any state changes.

## Remote Development Rules

- Treat your local shell, Python argparse, the remote transport, the remote shell, and the remote program as separate quoting layers.
- Do not pass here-strings, heredocs, multi-line Python, embedded YAML, or nested quotes directly to `--command`. This can make argparse report `unrecognized arguments`.
- When a target file is large, prefer local round-trip editing (scp/download -> local edit -> upload) over inline remote patching.
- Use base64 for complex remote work. Build the script locally, base64 encode it locally, then remotely run `printf '%s' '<base64>' | base64 -d > /tmp/codex_remote_task.sh && bash /tmp/codex_remote_task.sh`.
- Keep the remote decoder command short and single-line.
- Prefer writing a temporary remote script under `/tmp` and executing it over trying to inline complicated commands.
- Avoid remote commands with unescaped pipes, regex alternation, command substitutions, redirections, or wildcard-heavy expressions when using shell tools that may split or inspect command segments.
- For command output inspection, prefer simple commands such as `sed -n '1,120p' file`, `cat file`, `find dir -maxdepth N -type f`, and separate `grep` calls.
- For long-running services, do not start an interactive process unless the user explicitly asked for it. Use `nohup`, `systemd`, `tmux`, or write a start command to a log file.
- After remote file edits, verify with `sed`, `grep`, or checksums.
- If a local shell command fails before contacting the remote host, state that no remote change occurred.
- For remote desktop pop-up requests (GUI apps on the remote Linux desktop), launch commands with the desktop session environment by default:
  - `DISPLAY=:1`
  - `XAUTHORITY=/run/user/1000/gdm/Xauthority`
  - `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`
  - Add `LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/lib/aarch64-linux-gnu` when viewer tools depend on system OpenGL/USB libs.
  - Example pattern: `DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus <gui_command>`

## Guardrails

- Do not start a bare interactive session such as `plink user@host` or `ssh user@host` from the shell tool. It can hang waiting for input.
- Default to non-interactive behavior (`-batch` for plink).
- Do not bypass host key verification. If the host key is not already trusted, use a saved session or provide `--hostkey` (plink backend).
- For remote privilege escalation, prefer `sudo -n` so failures are explicit. If the remote host requires an interactive sudo password, stop and ask the user.
- Keep remote writes scoped and explicit. Confirm destructive actions and target paths before running them.
- Start with read-only inspection if the remote system state is unclear.
- Never use remote destructive operations such as recursive delete, reset, or overwrite without an explicit target check and user approval.
- Do not store passwords in the skill, repo, log files, or remote scripts. Use local environment variables.

## Command Patterns

### Setup (one-time per machine)

```bash
python scripts/setup.py
```

### Basic Commands

Saved session:
```bash
python scripts/invoke_remote.py --session my-host --shell bash --command "hostname && uname -a"
```

Explicit host with key:
```bash
python scripts/invoke_remote.py --host 192.168.1.50 --user nvidia --key ~/.ssh/id_ed25519 --shell bash --command "pwd && ls -la"
```

Pinned host key (plink backend only):
```powershell
python scripts/invoke_remote.py --host 192.168.1.50 --user nvidia --hostkey "ssh-ed25519 255 SHA256:..." --shell bash --command "systemctl status my-service"
```

Password via environment variable:
```bash
# macOS/Linux
export SSHPASS="example-password"
python scripts/invoke_remote.py --host 192.168.1.50 --user nvidia --password-env SSHPASS --shell bash --command "df -h"

# Windows
$env:PLINK_PASSWORD = "example-password"
python scripts/invoke_remote.py --host 192.168.1.50 --user nvidia --password-env PLINK_PASSWORD --shell bash --command "df -h"
```

### Force a Specific Backend

```bash
python scripts/invoke_remote.py --backend plink --session my-host --command "hostname"
python scripts/invoke_remote.py --backend sshpass --host 10.0.0.1 --user admin --key ~/.ssh/id_rsa --command "hostname"
```

### Complex Remote Scripts via Base64

```bash
script='set -euo pipefail
hostname
python3 - <<'\''PY'\''
from pathlib import Path
Path("/tmp/remote_test.txt").write_text("hello from remote-ops\n")
PY'

encoded=$(printf '%s' "$script" | base64)
python scripts/invoke_remote.py \
  --host 192.168.1.50 --user nvidia --key ~/.ssh/id_ed25519 \
  --shell bash \
  --command "printf '%s' '$encoded' | base64 -d > /tmp/task.sh && bash /tmp/task.sh"
```

See [references/usage.md](references/usage.md) for direct command examples, first-use guidance, and troubleshooting patterns.

## Resources

- `scripts/invoke_remote.py` — Builds safe one-shot remote commands for plink or sshpass/ssh backends. Resolves tool paths, supports saved sessions, explicit hosts, keys, hostkey pinning, env-based passwords, `bash`/`sh`/`raw` shells, optional working directory, and optional `sudo -n`.
- `scripts/setup.py` — One-time cross-platform setup hook. Detects OS and architecture, installs the correct tool.
- `references/usage.md` — Direct command patterns, complex script templates, and troubleshooting.

When the user wants this behavior explicitly, invoke the skill as `$remote-ops`.
