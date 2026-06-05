# remote-ops

跨平台远程主机操作技能包，适用于 Claude Code、Codex CLI 和 OpenCode。

非交互式远程命令执行：巡检、部署、日志采集、服务管理——无需建立交互式终端会话。

[English](README.md)

## 安装

```bash
curl -sSL https://raw.githubusercontent.com/timeuser4/remote-ops/v0.1/install.sh | bash
```

安装脚本会自动检测你已安装的 harness（`~/.claude`、`~/.codex`、`~/.opencode`），并一次性安装到所有检测到的 harness 中。

<details>
<summary>手动安装</summary>

```bash
TMP_DIR=$(mktemp -d)
git clone --depth 1 https://github.com/timeuser4/remote-ops.git "$TMP_DIR"
mkdir -p ~/.claude/skills/remote-ops
cp "$TMP_DIR/SKILL.md" ~/.claude/skills/remote-ops/
cp -r "$TMP_DIR/scripts/" "$TMP_DIR/references/" "$TMP_DIR/agents/" ~/.claude/skills/remote-ops/
python3 ~/.claude/skills/remote-ops/scripts/setup.py
rm -rf "$TMP_DIR"
```

将 `~/.claude` 替换为 `~/.codex` 或 `~/.opencode` 即可。
</details>

## 后端

| 系统 | 工具 | 来源 |
|------|------|------|
| Windows x64 / ARM64 / x86 | `plink.exe` (PuTTY) | setup.py 自动下载安装 |
| macOS | `sshpass` | Homebrew |
| Linux | `sshpass` | apt / dnf / pacman / zypper |

## 用法

### 密钥认证（推荐）

```bash
python3 ~/.codex/skills/remote-ops/scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --key ~/.ssh/id_ed25519 \
  --shell bash --command "hostname && df -h"
```

### 密码认证（通过环境变量）

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

### sudo 提权

```bash
python3 ~/.codex/skills/remote-ops/scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --key ~/.ssh/id_ed25519 \
  --sudo --shell bash --command "systemctl restart docker"
```

## 参数

| 参数 | 说明 |
|------|------|
| `--backend plink\|sshpass` | 强制指定后端，默认按 OS 自动选择 |
| `--session NAME` | 保存的会话名（PuTTY session 或 SSH config Host） |
| `--host IP` | 目标主机地址 |
| `--user NAME` | SSH 用户名 |
| `--port N` | SSH 端口（默认 22） |
| `--key PATH` | 密钥路径（plink 用 .ppk，sshpass 用标准 SSH key） |
| `--hostkey FINGERPRINT` | 主机密钥指纹（仅 plink 后端） |
| `--password-env VAR` | 存放密码的环境变量名 |
| `--shell bash\|sh\|raw` | 远端 shell 包装模式（默认 bash） |
| `--cwd PATH` | 远端工作目录 |
| `--sudo` | 用 `sudo -n` 包装远端命令 |
| `--dry-run` | 仅打印命令，不执行 |
| `--command CMD` | 远端要执行的命令（必填） |

## 安全规范

- **密码不写入命令行**——通过 `--password-env` 传入，`--dry-run` 输出会自动掩码
- **主机密钥验证不绕过**——plink 需要 `-hostkey`，ssh 使用 `StrictHostKeyChecking=accept-new`
- **不启动交互式会话**——始终使用非交互模式（`-batch` / `BatchMode=yes`）
- **sudo 明确失败**——使用 `sudo -n`，不等待交互式密码输入
- **远端写入先验证**——修改前建备份，写入后用独立只读命令验证

## 许可证

MIT
