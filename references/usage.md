# Remote Ops Usage

## Preconditions

- Run `python scripts/setup.py` once per machine.
- **Windows**: plink.exe from PuTTY (auto-downloaded by setup).
- **macOS**: sshpass (installed via Homebrew by setup).
- **Linux**: sshpass (installed via package manager by setup).
- Preferred auth order:
  - Key-based auth (`--key`)
  - SSH agent / Pageant
  - Password from environment variable (`--password-env`)

## Quick Checks

```powershell
# Windows
Get-Command plink -ErrorAction SilentlyContinue
python scripts/invoke_remote.py --session my-host --command "hostname" --dry-run
```

```bash
# macOS / Linux
which sshpass
python scripts/invoke_remote.py --host 192.168.1.50 --user admin --command "hostname" --dry-run
```

## Helper Script Examples

### Plink Backend (Windows)

Saved session:
```powershell
python scripts/invoke_remote.py `
  --session my-host `
  --shell bash `
  --command "hostname && uname -a && pwd"
```

Explicit host and PPK key:
```powershell
python scripts/invoke_remote.py `
  --host 192.168.1.50 `
  --user nvidia `
  --key C:\keys\board.ppk `
  --shell bash `
  --command "ls -la /opt && df -h"
```

Pinned host key (plink only):
```powershell
python scripts/invoke_remote.py `
  --host 192.168.1.50 `
  --user nvidia `
  --hostkey "ssh-ed25519 255 SHA256:..." `
  --shell bash `
  --command "systemctl status docker"
```

Password via environment variable (plink):
```powershell
$env:PLINK_PASSWORD = "example-password"
python scripts/invoke_remote.py `
  --host 192.168.1.50 `
  --user nvidia `
  --password-env PLINK_PASSWORD `
  --shell bash `
  --command "whoami && id"
Remove-Item Env:PLINK_PASSWORD
```

### sshpass Backend (macOS / Linux)

Explicit host with SSH key:
```bash
python scripts/invoke_remote.py \
  --host 192.168.1.50 \
  --user nvidia \
  --key ~/.ssh/id_ed25519 \
  --shell bash \
  --command "ls -la /opt && df -h"
```

Saved session (SSH config Host entry):
```bash
python scripts/invoke_remote.py \
  --session my-server \
  --shell bash \
  --command "hostname && uptime"
```

Password via environment variable (sshpass):
```bash
export SSHPASS="example-password"
python scripts/invoke_remote.py \
  --host 192.168.1.50 \
  --user nvidia \
  --password-env SSHPASS \
  --shell bash \
  --command "whoami && id"
unset SSHPASS
```

Custom port:
```bash
python scripts/invoke_remote.py \
  --host 192.168.1.50 \
  --port 2222 \
  --user admin \
  --key ~/.ssh/mykey \
  --shell bash \
  --command "pwd"
```

### Both Backends

Remote command with `sudo -n`:
```bash
python scripts/invoke_remote.py \
  --session my-host \
  --shell bash \
  --sudo \
  --command "systemctl restart my-service && systemctl status --no-pager my-service"
```

Dry run (print command, do not execute):
```bash
python scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --command "hostname" \
  --dry-run
```

Force specific backend:
```bash
# Force plink even on macOS/Linux (if plink is available via Wine or similar)
python scripts/invoke_remote.py --backend plink --session my-host --command "hostname"

# Force sshpass even on Windows (if sshpass is available via WSL/Cygwin)
python scripts/invoke_remote.py --backend sshpass --host 10.0.0.1 --user admin --command "hostname"
```

## Auto Base64 (`--base64`)

The `--base64` flag automatically encodes the `--command` value as base64 and pipes it through the remote decoder. This eliminates all shell escaping problems — pipes, redirects, quotes, dollar signs, and heredocs pass through cleanly.

```bash
# Simple pipe-through-base64
python scripts/invoke_remote.py \
  --host 192.168.1.50 --user nvidia --key ~/.ssh/id_ed25519 \
  --base64 --command 'echo "$PATH" | tr ":" "\n" | grep -i python'

# Multi-line script with heredoc, no manual encoding needed
python scripts/invoke_remote.py \
  --host 192.168.1.50 --user nvidia --key ~/.ssh/id_ed25519 \
  --base64 --command '
set -euo pipefail
hostname && date
python3 - <<'\''PY'\''
from pathlib import Path
Path("/tmp/remote_test.txt").write_text("hello from remote-ops\n")
PY
cat /tmp/remote_test.txt'

# Complex command with pipes and nested quotes
python scripts/invoke_remote.py \
  --host 192.168.1.50 --user nvidia --key ~/.ssh/id_ed25519 \
  --base64 --command "find /var/log -name '*.log' -mtime -7 | xargs wc -l | sort -rn | head -20"
```

## Remote Agent (`--agent`)

The remote agent provides structured I/O on the remote host. It is auto-deployed to `/tmp/remote-ops-agent.py` on first use. The agent is a one-shot Python script that reads a JSON request from stdin, executes the operation, and writes a JSON response to stdout.

### Protocol

**Request** (one JSON line to stdin):
```json
{"method": "<name>", "id": "<request-id>", "params": { ... }}
```

**Response** (one JSON line to stdout):
```json
{"ok": true, "data": { ... }, "id": "<request-id>"}
{"ok": false, "error": "description", "id": "<request-id>"}
```

### Methods

**ping** — Health check, returns host info.
```bash
python scripts/invoke_remote.py --host 10.0.0.1 --user admin --key ~/.ssh/id_ed25519 \
  --agent --command '{"method":"ping"}'
```
Response: `{"ok": true, "data": {"hostname": "...", "python": "...", "platform": "...", "cwd": "...", "uid": ...}}`

**exec** — Execute a shell command.
```bash
python scripts/invoke_remote.py --host 10.0.0.1 --user admin --key ~/.ssh/id_ed25519 \
  --agent --command "df -h && free -m"
```
Parameters: `command` (required), `shell` (bash/sh), `cwd`, `timeout` (seconds, default 60), `env` (dict).

Response: `{"ok": true, "data": {"ok": true, "stdout": "...", "stderr": "...", "returncode": 0}}`

**read** — Read a file, content returned as base64.
```bash
python scripts/invoke_remote.py --host 10.0.0.1 --user admin --key ~/.ssh/id_ed25519 \
  --agent --command '{"method":"read","params":{"path":"/etc/hostname"}}'
```
Response: `{"ok": true, "data": {"ok": true, "data": "<base64>", "size": 42}}`

**write** — Write a file from base64-encoded data.
```bash
python scripts/invoke_remote.py --host 10.0.0.1 --user admin --key ~/.ssh/id_ed25519 \
  --agent --command '{"method":"write","params":{"path":"/tmp/test.txt","data":"SGVsbG8gV29ybGQK","mode":"644"}}'
```
Parameters: `path` (required), `data` (base64, required), `mode` (octal string, default "644").

**list** — List directory contents.
```bash
python scripts/invoke_remote.py --host 10.0.0.1 --user admin --key ~/.ssh/id_ed25519 \
  --agent --command '{"method":"list","params":{"path":"/var/log"}}'
```
Parameters: `path` (required), `pattern` (glob, default "*").
Response: `{"ok": true, "data": {"ok": true, "entries": [{"name": "...", "path": "...", "type": "file|dir", "size": N, "mtime": T}, ...]}}`

### Deployment

On first `--agent` invocation per remote host:
1. Checks for Python >= 3.7 on the remote
2. Reads the local `scripts/remote_agent.py`
3. Base64-encodes and writes it to the remote path (default `/tmp/remote-ops-agent.py`)
4. Sets the executable bit

Subsequent calls detect the existing agent and skip deployment. Deployment adds ~1-2 seconds to the first call.

**checksum** — Get SHA256 hash of a remote file (used internally by `--sync`).
```bash
python scripts/invoke_remote.py --host 10.0.0.1 --user admin --key ~/.ssh/id_ed25519 \
  --agent --command '{"method":"checksum","params":{"path":"/etc/hostname"}}'
```
Parameters: `path` (required).
Response (file exists): `{"ok": true, "data": {"ok": true, "sha256": "abc123...", "size": 42}}`
Response (file not found): `{"ok": true, "data": {"ok": true, "sha256": null, "size": 0}}`

## File Sync (`--sync`)

Push a local file to remote, checksum-based: only transfers if the remote copy differs or doesn't exist.

```bash
# Push local file to remote
python scripts/invoke_remote.py \
  --host 10.0.0.1 --user admin --key ~/.ssh/id_ed25519 \
  --sync ./config.yaml:/etc/app/config.yaml

# With saved server profile
python scripts/invoke_remote.py \
  --server my-vm --sync ./deploy.sh:/opt/bin/deploy.sh

# Dry-run to preview
python scripts/invoke_remote.py \
  --server my-vm --sync ./script.py:/opt/script.py --dry-run
```

Flow: local SHA256 → remote `checksum` → compare → if different, `write` → verify.

## Server DB

Save, list, and reuse server connection profiles stored in `~/.remote-ops/servers.json`.

```bash
# Save a server for later reuse
python scripts/invoke_remote.py \
  --host 192.168.1.50 --user nvidia --key ~/.ssh/id_ed25519 \
  --save my-nvidia-box --command "hostname"

# List saved servers
python scripts/invoke_remote.py --list-servers

# Use a saved server
python scripts/invoke_remote.py --server my-nvidia-box --command "df -h"

# Override saved values
python scripts/invoke_remote.py --server my-nvidia-box --user root --command "whoami"

# Delete a saved server
python scripts/invoke_remote.py --delete-server my-nvidia-box
```

## Direct Command Patterns (Without Helper Script)

### Plink (Windows)

Saved session:
```powershell
plink -batch -load my-host "bash -lc 'hostname && uname -a'"
```

Explicit host and key:
```powershell
plink -batch -ssh -i C:\keys\board.ppk nvidia@192.168.1.50 "bash -lc 'pwd && ls -la'"
```

Pinned host key:
```powershell
plink -batch -ssh -hostkey "ssh-ed25519 255 SHA256:..." nvidia@192.168.1.50 "bash -lc 'journalctl -n 50 --no-pager'"
```

### sshpass (macOS / Linux)

Password auth:
```bash
sshpass -e ssh -o StrictHostKeyChecking=accept-new nvidia@192.168.1.50 "bash -lc 'hostname'"
```

Key auth:
```bash
ssh -o StrictHostKeyChecking=accept-new -i ~/.ssh/id_ed25519 nvidia@192.168.1.50 "bash -lc 'hostname'"
```

## Notes

- The backend is auto-detected from OS but can be forced with `--backend`.
- `--hostkey` is only supported by the plink backend.
- Avoid bare `plink host` or `ssh host` interactive sessions when using the shell tool.
- Use `bash -lc` when the remote target is Linux and the command needs shell features.
- Use `--shell raw` only when the remote side should receive the command exactly as written.
- If `sudo -n` fails, the remote host likely requires a password prompt. Stop and ask the user.
- **Use `--base64` for any command with special characters** — pipes, redirects, quotes, dollar signs, JSON, heredocs. This eliminates quoting issues across all shell layers.
- **Use `--agent` for structured remote work** — file reads/writes, directory listings, complex scripts. The agent handles encoding/decoding internally.
- `--base64` and `--agent` are mutually exclusive.
- For remote writes, back up target files first and verify with a separate read-only command.
- Server profiles are stored in `~/.remote-ops/servers.json` and managed via `--save`, `--list-servers`, `--delete-server`, and `--server`.
