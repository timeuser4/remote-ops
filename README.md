# remote-ops

Cross-platform remote host operations skill for Claude Code & Codex CLI.

跨平台远程主机操作技能包，适用于 Claude Code 和 Codex CLI。

- **Windows**: `plink.exe` (PuTTY Link)，自动检测 CPU 架构下载对应安装包
- **macOS / Linux**: `sshpass` + `ssh`

One-shot remote inspection, deployment, log collection, and service management without interactive terminal sessions.

非交互式远程命令执行：巡检、部署、日志采集、服务管理，不建立交互式终端会话。

## Install / 安装

### Claude Code

**Clone 安装：**

```bash
git clone https://github.com/timeuser4/remote-ops.git
mkdir -p ~/.claude/skills/remote-ops
cp -r remote-ops/SKILL.md remote-ops/scripts/ remote-ops/references/ remote-ops/agents/ ~/.claude/skills/remote-ops/
python3 ~/.claude/skills/remote-ops/scripts/setup.py
```

**npm 安装（TODO）：**

```bash
npm install -g remote-ops-skill
mkdir -p ~/.claude/skills/remote-ops
cp -r "$(npm root -g)/remote-ops-skill/"* ~/.claude/skills/remote-ops/
python3 ~/.claude/skills/remote-ops/scripts/setup.py
```

### Codex CLI

**Clone 安装：**

```bash
git clone https://github.com/timeuser4/remote-ops.git
mkdir -p ~/.codex/skills/remote-ops
cp -r remote-ops/SKILL.md remote-ops/scripts/ remote-ops/references/ remote-ops/agents/ ~/.codex/skills/remote-ops/
python3 ~/.codex/skills/remote-ops/scripts/setup.py
```

**npm 安装（TODO）：**

```bash
npm install -g remote-ops-skill
mkdir -p ~/.codex/skills/remote-ops
cp -r "$(npm root -g)/remote-ops-skill/"* ~/.codex/skills/remote-ops/
python3 ~/.codex/skills/remote-ops/scripts/setup.py
```

### OpenCode

**Clone 安装：**

```bash
git clone https://github.com/timeuser4/remote-ops.git
mkdir -p ~/.opencode/skills/remote-ops
cp -r remote-ops/SKILL.md remote-ops/scripts/ remote-ops/references/ remote-ops/agents/ ~/.opencode/skills/remote-ops/
python3 ~/.opencode/skills/remote-ops/scripts/setup.py
```

**npm 安装（TODO）：**

```bash
npm install -g remote-ops-skill
mkdir -p ~/.opencode/skills/remote-ops
cp -r "$(npm root -g)/remote-ops-skill/"* ~/.opencode/skills/remote-ops/
python3 ~/.opencode/skills/remote-ops/scripts/setup.py
```

## Usage / 用法

### macOS / Linux（sshpass 后端）

```bash
# 密钥认证
python3 ~/.codex/skills/remote-ops/scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --key ~/.ssh/id_ed25519 \
  --shell bash --command "hostname && df -h"

# 密码认证（通过环境变量，不写入命令行）
export SSHPASS="your-password"
python3 ~/.codex/skills/remote-ops/scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --password-env SSHPASS \
  --shell bash --command "systemctl status nginx"
unset SSHPASS

# sudo 提权
python3 ~/.codex/skills/remote-ops/scripts/invoke_remote.py \
  --host 192.168.1.50 --user admin --key ~/.ssh/id_ed25519 \
  --sudo --shell bash --command "systemctl restart docker"
```

### Windows（plink 后端）

```powershell
# 密钥认证
python ~/.codex/skills/remote-ops/scripts/invoke_remote.py `
  --host 192.168.1.50 --user nvidia --key C:\keys\board.ppk `
  --shell bash --command "pwd && ls -la"

# 密码认证
$env:PLINK_PASSWORD = "example-password"
python ~/.codex/skills/remote-ops/scripts/invoke_remote.py `
  --host 192.168.1.50 --user nvidia --password-env PLINK_PASSWORD `
  --shell bash --command "whoami && id"
Remove-Item Env:PLINK_PASSWORD
```

### 公共参数

| 参数 | 说明 |
|------|------|
| `--backend plink\|sshpass` | 强制指定后端，省略时按 OS 自动选择 |
| `--session NAME` | 保存的会话名（PuTTY session 或 SSH config Host） |
| `--host IP` | 目标主机地址 |
| `--user NAME` | SSH 用户名 |
| `--port N` | SSH 端口，默认 22 |
| `--key PATH` | 密钥路径（plink 用 .ppk，sshpass 用标准 SSH key） |
| `--hostkey FINGERPRINT` | 固定主机密钥指纹（仅 plink 后端） |
| `--password-env VAR` | 存放密码的环境变量名 |
| `--shell bash\|sh\|raw` | 远端 shell 包装模式，默认 bash |
| `--cwd PATH` | 远端工作目录 |
| `--sudo` | 用 `sudo -n` 包装远端命令 |
| `--dry-run` | 仅打印命令，不执行 |
| `--command CMD` | 远端要执行的命令（必填） |

## Backends / 后端

| 系统 | 后端 | 工具 | 来源 |
|------|------|------|------|
| Windows x64 | plink | `putty-64bit-0.83-installer.msi` | [PuTTY 官方](https://the.earth.li/~sgtatham/putty/latest/) |
| Windows ARM64 | plink | `putty-arm64-0.83-installer.msi` | [PuTTY 官方](https://the.earth.li/~sgtatham/putty/latest/) |
| Windows x86 | plink | `putty-0.83-installer.msi` | [PuTTY 官方](https://the.earth.li/~sgtatham/putty/latest/) |
| macOS | sshpass | `brew install sshpass` | Homebrew |
| Linux | sshpass | apt/dnf/pacman/zypper | 系统包管理器 |

## 安全规范

- **密码不写入命令行**：仅通过 `--password-env` 指定的环境变量传入，`--dry-run` 输出会自动掩码
- **主机密钥验证不绕过**：plink 需要 `-hostkey`，ssh 默认 `StrictHostKeyChecking=accept-new`
- **不启动交互式会话**：避免 shell 工具挂起，始终使用非交互模式（`-batch` / `BatchMode=yes`）
- **sudo 明确失败**：使用 `sudo -n`，不等待交互式密码输入
- **远端写入先备份**：修改文件前建备份，写入后用独立只读命令验证

## License / 许可证

MIT
