---
name: remote-ops
description: Use this skill whenever the user needs to run commands on a remote Linux host via SSH, inspect remote servers, deploy files, sync code, read/write remote files, or manage server connection profiles. Trigger for any remote execution task — one-shot commands, log inspection, service management, file transfer, or structured remote I/O. Supports plink (Windows) and sshpass/ssh (macOS/Linux) with automatic shell-safe transport (--base64), a deployable remote agent (--agent) for structured operations, checksum-based file sync (--sync), and persistent server profiles (--server/--save).
---

# Remote Ops

## Overview

Use this skill when work must be performed on a remote host from the local machine. It is optimized for one-shot remote commands and repeatable remote workflows, not long-lived interactive terminal sessions.

- **Windows**: uses `plink.exe` (PuTTY Link)
- **macOS / Linux**: uses `sshpass` + `ssh`

## When To Use

Trigger this skill for any task that involves executing commands on a remote host via SSH:

- The user asks to run a command on a remote server, inspect logs, check service status, or deploy files.
- The user mentions an SSH target (hostname, IP, saved session) and wants to do something there.
- The user needs structured remote operations: read/write files, list directories, sync local code to remote.
- The user wants to save a server connection profile for later reuse.
- Cross-platform: Windows (plink), macOS, or Linux (sshpass/ssh) clients are all supported.

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
   - Large files: use `--agent read` to pull content locally, edit, then `--agent write` or `--sync` to push back.
   - Small files: direct remote edits are acceptable.
   - Default heuristic: treat files over ~200 lines or ~8 KB as large unless the user specifies otherwise.
6. For any multi-line script, complex quoting, heredoc, JSON/YAML, or remote file edits, use `--base64` (automatic safe transport) or `--agent` (structured I/O with auto-deployment).
7. For remote writes, create backups first, write idempotently, and verify the resulting file contents with a second read-only command.
8. Report the exact remote command intent, the output, and any state changes.

## Remote Development Rules

- Treat your local shell, Python argparse, the remote transport, the remote shell, and the remote program as separate quoting layers.
- Use `--base64` for any command containing special characters (pipes, quotes, dollar signs, heredocs, JSON/YAML). The flag auto-encodes the command and eliminates all quoting issues.
- Use `--agent` for structured remote work (file read/write, directory listing, complex exec). The agent is auto-deployed to `/tmp/remote-ops-agent.py` on first use; subsequent calls detect it and skip deployment.
- When a target file is large, prefer `--agent` with read/write methods for local round-trip editing, or use scp/download -> local edit -> upload.
- Do not pass here-strings, heredocs, multi-line Python, embedded YAML, or nested quotes directly to `--command` without `--base64`.
- For command output inspection, prefer simple commands: `sed`, `cat`, `find`, and narrow `grep`. Use `--agent` for structured reads.
- For long-running services, do not start an interactive process unless the user explicitly asked for it. Use `nohup`, `systemd`, `tmux`, or write a start command to a log file.
- After remote file edits, verify with `sed`, `grep`, or checksums.
- If a local shell command fails before contacting the remote host, state that no remote change occurred.
- For remote desktop pop-up requests (GUI apps on the remote Linux desktop), launch commands with the desktop session environment. Typical defaults for Jetson / single-user Linux desktop, adjust UID and paths as needed for the target system:
  - `DISPLAY=:1`
  - `XAUTHORITY=/run/user/1000/gdm/Xauthority`
  - `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus`
  - Add `LD_LIBRARY_PATH=/usr/lib/aarch64-linux-gnu:/lib/aarch64-linux-gnu` when viewer tools depend on system OpenGL/USB libs.
  - Example pattern: `DISPLAY=:1 XAUTHORITY=/run/user/1000/gdm/Xauthority DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus <gui_command>`

## Guardrails

- Do not start a bare interactive session such as `plink user@host` or `ssh user@host` from the shell tool. It can hang waiting for input.
- Default to non-interactive behavior (`-batch` for plink).
- Host key policy: sshpass backend uses `accept-new` (trust on first use). plink backend accepts the key only when pinned via `--hostkey` or a saved session. For strict verification, pre-populate `~/.ssh/known_hosts` before connecting.
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

Password via environment variable:
```bash
export SSHPASS="example-password"
python scripts/invoke_remote.py --host 192.168.1.50 --user nvidia --password-env SSHPASS --shell bash --command "df -h"
```

Hostkey pinning, Windows plink, custom ports, and backend forcing: see `references/usage.md`.

### Complex Remote Scripts via Base64

The `--base64` flag auto-encodes the command to avoid all shell escaping issues — pipes, quotes, dollar signs, and multi-line scripts pass through cleanly.

```bash
python scripts/invoke_remote.py \
  --host 10.0.0.1 --user admin --key ~/.ssh/id_ed25519 \
  --base64 --command 'echo "$PATH" | tr ":" "\n" | grep -i python'
```

Multi-line scripts and heredoc patterns: see `references/usage.md`.

### Remote Agent (Structured I/O)

First use auto-deploys the agent to `/tmp/remote-ops-agent.py`. Subsequent calls detect the existing agent and skip deployment.

```bash
# Execute commands through the agent (auto-deploy on first use)
python scripts/invoke_remote.py \
  --host 10.0.0.1 --user admin --key ~/.ssh/id_ed25519 \
  --agent --command "df -h && free -m"
```

Agent methods: `ping`, `exec`, `read`, `write`, `list`, `checksum`.

For per-method examples (read/write/list/ping/checksum) and the full JSONL protocol, see `references/usage.md`.

### File Sync (`--sync`)

Push a local file to the remote host, checksum-based: only transfers if the remote copy differs.

```bash
python scripts/invoke_remote.py \
  --host 10.0.0.1 --user admin --key ~/.ssh/id_ed25519 \
  --sync ./my_script.py:/opt/app/my_script.py
```

Uses the remote agent internally — auto-deploys on first use. Output: `Already in sync` or `Synced: local -> remote (N bytes)`. See `references/usage.md`.

### Server DB (Local Persistence)

Save, list, and reuse server connection profiles via `~/.remote-ops/servers.json`.

```bash
# Save a server after connecting
python scripts/invoke_remote.py \
  --host 10.0.0.5 --user admin --key ~/.ssh/id_ed25519 \
  --save my-vm --command "hostname"

# Reuse a saved server
python scripts/invoke_remote.py --server my-vm --command "df -h"

# List all saved servers
python scripts/invoke_remote.py --list-servers
```

Override, delete, and notes: see `references/usage.md`.

### Troubleshooting

Common failure modes and what they mean:

```bash
# Python not available on remote
# ERROR: remote host does not have Python >= 3.7.
# Action: tell the user the remote host needs Python 3.7+. Fall back to --base64 for simple commands.

# Agent deployment failed (permissions / disk space)
# ERROR: failed to deploy agent to /tmp/remote-ops-agent.py
# Action: check remote /tmp permissions and disk space with a simple command.

# Agent request failed
# AGENT ERROR: Not a file: /some/path
# Action: verify the remote path exists. Use --agent --command '{"method":"list",...}' to explore.

# Connection failed before reaching remote
# ssh: Could not resolve hostname ...
# Action: state that no remote change occurred. Verify hostname, network, and SSH key.
```

See [references/usage.md](references/usage.md) for direct command examples, first-use guidance, and troubleshooting patterns.

## Resources

- `scripts/invoke_remote.py` — One-shot remote commands via plink or sshpass/ssh. Supports `--base64` for automatic shell-safe transport, `--agent` for structured remote execution with auto-deployment, `--server` for saved connection profiles, and `--save`/`--list-servers`/`--delete-server` for server DB management.
- `scripts/remote_agent.py` — Deployable remote agent providing structured I/O (ping/exec/read/write/list) via JSONL over stdin/stdout. Auto-deployed to the remote host by `--agent`.
- `scripts/server_db.py` — JSON-backed local server connection store.
- `scripts/setup.py` — One-time cross-platform setup hook.
- `references/usage.md` — Detailed command patterns, agent protocol, and troubleshooting.

When the user wants this behavior explicitly, invoke the skill as `$remote-ops`.