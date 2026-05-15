# remote-ops

Cross-platform remote host operations skill for Claude Code & Codex CLI.

- **Windows**: `plink.exe` (PuTTY Link) with auto-architecture detection
- **macOS / Linux**: `sshpass` + `ssh`

One-shot remote inspection, deployment, log collection, and service management without interactive terminal sessions.

## Install

```bash
# Clone
git clone https://github.com/timeuser4/remote-ops.git
mkdir -p ~/.codex/skills
ln -s "$(pwd)/remote-ops" ~/.codex/skills/remote-ops

# One-time setup (installs plink/sshpass)
python3 ~/.codex/skills/remote-ops/scripts/setup.py
```

Or via npm:

```bash
npm install -g remote-ops-skill  # TODO: update after npm publish
mkdir -p ~/.codex/skills
ln -s "$(npm root -g)/remote-ops-skill" ~/.codex/skills/remote-ops
python3 ~/.codex/skills/remote-ops/scripts/setup.py
```

## Usage

```bash
# Key-based auth
python3 ~/.codex/skills/remote-ops/scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --key ~/.ssh/id_ed25519 \
  --shell bash --command "hostname && df -h"

# Password via env var
export SSHPASS="your-password"
python3 ~/.codex/skills/remote-ops/scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --password-env SSHPASS \
  --shell bash --command "systemctl status nginx"
```

Windows (PowerShell + plink):

```powershell
python ~/.codex/skills/remote-ops/scripts/invoke_remote.py `
  --host 192.168.1.50 --user nvidia --key C:\keys\board.ppk `
  --shell bash --command "pwd && ls -la"
```

## Backends

| OS | Backend | Tool |
|----|---------|------|
| Windows x64 | plink | `putty-64bit-0.83-installer.msi` |
| Windows ARM64 | plink | `putty-arm64-0.83-installer.msi` |
| Windows x86 | plink | `putty-0.83-installer.msi` |
| macOS | sshpass | `brew install sshpass` |
| Linux | sshpass | apt/dnf/pacman/zypper |

## License

MIT
