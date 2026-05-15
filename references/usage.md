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

## Complex Remote Scripts

Do not pass multi-line scripts, here-strings, heredocs, YAML/JSON generation, or nested Python directly to `--command`. The local shell and argparse can split the payload before it reaches the remote host. Use a local base64 payload and a short remote decoder command.

### bash/zsh template (macOS / Linux):

```bash
export SSHPASS="example-password"

script='set -euo pipefail
hostname
date

# Put complex remote work here: heredocs, Python, YAML edits, pipes, redirects.
python3 - <<'\''PY'\''
from pathlib import Path
p = Path.home() / "remote_test.txt"
p.write_text("hello from remote-ops\n")
print(p)
PY'

encoded=$(printf '%s' "$script" | base64)
remote="tmp=/tmp/codex_remote_task_\$(date +%s).sh; printf '%s' '$encoded' | base64 -d > \$tmp && bash \$tmp"

python scripts/invoke_remote.py \
  --host 192.168.1.50 \
  --user nvidia \
  --password-env SSHPASS \
  --shell bash \
  --command "$remote"
```

### PowerShell template (Windows):

```powershell
$env:PLINK_PASSWORD = "example-password"

$script = @'
set -euo pipefail
hostname
date

python3 - <<'PY'
from pathlib import Path
p = Path.home() / "remote_test.txt"
p.write_text("hello from remote-ops\n")
print(p)
PY
'@

$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($script))
$remote = "tmp=/tmp/codex_remote_task_`$(date +%s).sh; printf '%s' '$encoded' | base64 -d > `$tmp && bash `$tmp"

python scripts/invoke_remote.py `
  --host 192.168.1.50 `
  --user nvidia `
  --password-env PLINK_PASSWORD `
  --hostkey "ssh-ed25519 255 SHA256:..." `
  --shell bash `
  --command $remote
```

### Verification pass:

```bash
python scripts/invoke_remote.py \
  --host 192.168.1.50 \
  --user nvidia \
  --key ~/.ssh/id_ed25519 \
  --shell bash \
  --command "sed -n '1,40p' ~/remote_test.txt"
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
- If the local invocation fails with `unrecognized arguments`, the remote command was probably not executed. Fix local quoting first.
- Prefer simple read-only inspection commands outside the base64 pattern: `hostname`, `pwd`, `sed`, `cat`, `find`, and narrow `grep`.
- Put pipes, redirections, command substitutions, heredocs, and multi-line edits inside the base64 payload.
- For remote writes, back up target files first and verify with a separate read-only command.
