# remote-ops

Cross-platform remote host operations skill for Claude Code, Codex CLI, and OpenCode.

Non-interactive remote command execution: inspection, deployment, log collection, service management — without opening an interactive terminal session.

[中文文档](README.zh-CN.md)

## Install

```bash
curl -sSL https://raw.githubusercontent.com/timeuser4/remote-ops/main/install.sh | bash
```

The installer auto-detects which harnesses you have (`~/.claude`, `~/.codex`, `~/.opencode`) and installs to all of them.

<details>
<summary>Manual install</summary>

```bash
TMP_DIR=$(mktemp -d)
git clone --depth 1 https://github.com/timeuser4/remote-ops.git "$TMP_DIR"
mkdir -p ~/.claude/skills/remote-ops
cp "$TMP_DIR/SKILL.md" ~/.claude/skills/remote-ops/
cp -r "$TMP_DIR/scripts/" "$TMP_DIR/references/" "$TMP_DIR/agents/" ~/.claude/skills/remote-ops/
python3 ~/.claude/skills/remote-ops/scripts/setup.py
rm -rf "$TMP_DIR"
```

Replace `~/.claude` with `~/.codex` or `~/.opencode` as needed.
</details>

## Backends

| OS | Tool | Source |
|----|------|--------|
| Windows x64 / ARM64 / x86 | `plink.exe` (PuTTY) | Auto-downloaded by setup |
| macOS | `sshpass` | Homebrew |
| Linux | `sshpass` | apt / dnf / pacman / zypper |

## Usage

### Key auth (recommended)

```bash
python3 ~/.codex/skills/remote-ops/scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --key ~/.ssh/id_ed25519 \
  --shell bash --command "hostname && df -h"
```

### Password via environment variable

```bash
export SSHPASS="your-password"
python3 ~/.codex/skills/remote-ops/scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --password-env SSHPASS \
  --shell bash --command "systemctl status nginx"
unset SSHPASS
```

### Windows (PowerShell)

```powershell
python ~/.codex/skills/remote-ops/scripts/invoke_remote.py `
  --host 192.168.1.50 --user admin --key C:\keys\board.ppk `
  --shell bash --command "pwd && ls -la"
```

### With sudo

```bash
python3 ~/.codex/skills/remote-ops/scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --key ~/.ssh/id_ed25519 \
  --sudo --shell bash --command "systemctl restart docker"
```

## Parameters

| Parameter | Description |
|-----------|-------------|
| `--backend plink\|sshpass` | Force backend (auto-detected from OS by default) |
| `--session NAME` | Saved session name (PuTTY session or SSH config Host) |
| `--host IP` | Target host address |
| `--user NAME` | SSH username |
| `--port N` | SSH port (default 22) |
| `--key PATH` | Key path (.ppk for plink, standard SSH key for sshpass) |
| `--hostkey FINGERPRINT` | Host key fingerprint (plink backend only) |
| `--password-env VAR` | Environment variable holding the SSH password |
| `--shell bash\|sh\|raw` | Remote shell wrapper (default bash) |
| `--cwd PATH` | Remote working directory |
| `--sudo` | Wrap command with `sudo -n` |
| `--dry-run` | Print command without executing |
| `--command CMD` | Remote command to execute (required) |

## Security

- **Passwords never appear in command lines** — use `--password-env`; `--dry-run` output masks password values
- **Host key verification is not bypassed** — plink requires `-hostkey`, ssh uses `StrictHostKeyChecking=accept-new`
- **No interactive sessions** — always uses non-interactive mode (`-batch` / `BatchMode=yes`)
- **sudo fails explicitly** — uses `sudo -n`, never waits for interactive password
- **Remote writes are verified** — backups created before modification, verified with a second read-only command

## License

MIT
