---
name: remote-ops
version: 0.2
description: Cross-platform remote host operations skill for Claude Code & Codex CLI. Uses rtmux (SSH + tmux) as the core engine — replaces sshpass and plink. All commands execute inside persistent tmux sessions on the remote host, preserving terminal history, working directory, and environment context across disconnects. Supports command execution, file copy with resume, base64 safe transport, structured JSON output with connection context, jump host (bastion) support, and session binding.
---

# Remote Ops

## Overview

Use this skill when work must be performed on a remote host from the local machine. Powered by **rtmux** (SSH + tmux), all commands execute inside persistent tmux sessions, preserving terminal history, working directory, and environment variables across SSH disconnects.

- **Core engine**: rtmux (paramiko SSH + remote tmux)
- **Replaces**: sshpass, plink, raw ssh commands
- **Session persistence**: tmux sessions survive disconnects
- **Cross-platform client**: macOS, Linux, Windows (paramiko-based SSH)

- **All commands execute inside tmux sessions** — no command escapes session management
- **Session context persists** — terminal history, cwd, env vars survive disconnects
- **Base64 safe transport** — `--base64` avoids all shell escaping issues
- **Structured JSON output** — `--json` includes host/session context for easy identification
- **Cross-platform** — macOS and Linux clients (paramiko-based SSH)

## When To Use

Trigger this skill for any task that involves executing commands on a remote host via SSH:

- The user asks to run a command on a remote server, inspect logs, check service status, or deploy files.
- The user mentions an SSH target (hostname, IP, saved connection) and wants to do something there.
- The user needs file operations: copy files to/from remote, list, delete on remote hosts.
- The user needs persistent terminal sessions that survive SSH disconnects.
- The user wants to resume work on a remote host from a previous session.
- The user needs to execute complex commands with pipes, quotes, heredocs, or multi-line scripts.

## Preconditions

- Install rtmux locally: run `python3 setup_rtmux.py` (creates venv, installs paramiko + rtmux).
- Remote host must have bash available (tmux is auto-installed if missing).
- SSH key-based auth is preferred. See **Connection Setup** below for initialization flow.
- Ask for missing target details when they cannot be inferred safely:
  - host alias (from `rtmux connections`)
  - port if not `22`

## Connection Setup

When the user asks to connect to a remote host, follow this flow:

### 1. Check existing connection

```bash
rtmux --json connections
```

If a matching connection exists (same user@host), the SSH key was created during setup — **use it directly**, skip to the Workflow section.

If no matching connection → proceed to step 2.

### 2. No connection found — initialize

SSH key does not exist yet. Check if the user provided enough info:

**Full info provided (user + host + password)** → automate:

```bash
rtmux connect user@host --port <port> --password '<password>'
```

**Partial info (no password)** → give the user the command:

```
请在终端执行以下命令完成连接初始化：

rtmux connect user@host --port <port>

完成后告诉我，或者直接告诉我密码，我来完成。
```

If the user later provides the password instead of saying "done", accept it and run the connect command.

### 3. Connection ready

After initialization, verify with a smoke test:

```bash
rtmux --json exec agent-test "hostname && uname -a" --host <alias> --auto-create
```

Then proceed with the actual task.

## Session Naming Convention

The agent generates a meaningful session name for each task. This is the agent's responsibility — rtmux does not auto-generate names.

**Format**: `agent-{task-slug}` or `agent-{task-slug}-{qualifier}`

**Examples**:
- `agent-debug-nginx`
- `agent-deploy-frontend`
- `agent-check-disk`
- `agent-setup-python-env`

**Rules**:
- Use only alphanumeric characters, hyphens, and underscores
- Keep it under 64 characters
- Generate the name at task start, reuse it for all calls within the task
- Use `--auto-create` on the first `exec` call to create the session automatically
- Use a different session name per independent task
- One conversation can have multiple sessions for different tasks

**Session Binding Rules**:
1. At conversation start, pick ONE session name for the entire task
2. Reuse it for ALL rtmux commands in this conversation
3. If user specifies a session name, use that instead
4. If user says "use existing session", run `rtmux --json list` first to find it
5. At conversation end, leave session alive — user may want to resume later

## Workflow

1. **Connection check**: Follow **Connection Setup** above if the target host has no saved connection.
2. **Discover connections**: `rtmux --json connections` to find available host aliases.
3. **Choose session name**: Pick a meaningful name for the task.
3. **Smoke test**: `rtmux --json exec {session} "hostname && uname -a" --host {alias} --auto-create`
4. **Execute commands**: Use `rtmux --json exec {session} "{command}" --host {alias}` for all operations.
5. **Complex commands**: Use `--base64` for commands with pipes, quotes, `$`, heredocs, multi-line scripts.
6. **File operations**: Use `rtmux cp` (upload/download), `ls`, `rm` as needed.
7. **Cleanup**: When the task is complete:
   ```bash
   rtmux --json kill {session} --host {alias}
   ```
8. **Or preserve**: Leave the session alive. Find it later via `rtmux --json list --host {alias}`.

## Command Reference

### Global Options

- `--json` — JSON output with connection context (host, hostname, username, session)
- Position: **before** the subcommand: `rtmux --json exec ...`

### Connection Discovery

```bash
rtmux --json connections
# {"status":"ok","connections":[{"alias":"...","hostname":"...","username":"...","port":22}]}
```

### Command Execution

```bash
# Basic exec
rtmux --json exec my-session "df -h" --host server1

# Auto-create session if not exists
rtmux --json exec my-session "hostname" --host server1 --auto-create

# Custom timeout (default 30s)
rtmux --json exec my-session "slow-task" --host server1 --timeout 120

# Base64 safe transport (for complex commands)
rtmux --json exec my-session 'echo "hello $USER" | grep hello' --host server1 --base64
```

JSON output:
```json
{"host":"server1","hostname":"10.0.0.1","username":"admin","session":"my-session","status":"success","output":"hello admin","exit_code":0}
```

### Base64 Mode

Use `--base64` for any command containing special characters. The command is base64-encoded locally, decoded and executed on the remote host. No shell escaping issues.

```bash
# Pipes, quotes, $variables
rtmux --json exec s1 'echo "hello $USER" | grep hello' --host server1 --base64

# Multi-line / loops
rtmux --json exec s1 'for i in 1 2 3; do echo "item $i"; done' --host server1 --base64

# Heredoc / YAML / JSON content
rtmux --json exec s1 'cat > /tmp/c.yaml << "EOF"
key: "value with spaces"
list: [1, 2, 3]
EOF' --host server1 --base64

# Command substitution
rtmux --json exec s1 'echo "uptime: $(uptime -p)"' --host server1 --base64
```

### Session Management

```bash
# List sessions
rtmux --json list --host server1
# {"host":"server1","hostname":"...","username":"...","status":"ok","sessions":[{"name":"...","windows":1,"created":"..."}]}

# Kill session
rtmux --json kill my-session --host server1

# Capture terminal history
rtmux --json capture my-session --host server1
rtmux --json capture my-session --host server1 --lines 100

# Create session explicitly (usually use --auto-create instead)
rtmux --json new my-session --host server1
```

### File Copy (cp)

类似 scp，用 `::` 前缀标识远程路径（双冒号避免 Windows 盘符冲突）：

```bash
# 上传文件
rtmux --json cp ./local/file.txt ::/remote/path --host server1

# 上传目录（递归）
rtmux --json cp ./local/dir ::/remote/path -r --host server1

# 下载文件
rtmux --json cp ::/remote/file.txt ./local/path --host server1

# 下载目录（递归）
rtmux --json cp ::/remote/dir ./local/path -r --host server1

# 断点续传（大文件中断后重跑）
rtmux --json cp ./big-file.tar.gz ::/remote/path --host server1 --resume
```

### Other File Operations

```bash
# List
rtmux --json ls /remote/path --host server1

# Delete (JSON mode skips confirmation)
rtmux --json rm /remote/path --host server1

# Proxy download (URL downloaded directly on remote)
rtmux --json proxy-dl https://example.com/file /remote/path --host server1
```

### Connection Management

```bash
# Set up connection (interactive password)
rtmux connect user@hostname --port 6000

# Set up connection (password from env var)
SSHPASS="xxx" rtmux connect user@hostname --password-env SSHPASS

# Set up connection via jump host (bastion)
rtmux connect user@target --via bastion-alias

# Set up connection via multiple jump hosts (chain)
rtmux connect user@target --via bastion1,bastion2

# Remove connection
rtmux disconnect <alias>
rtmux disconnect <alias> --remove-key
```

### Jump Host (跳板机)

When the target host is only accessible through a bastion/jump host, use `--via` to specify the jump host chain.

**Setup flow**:
1. First, set up the jump host: `rtmux connect user@bastion`
2. Then, set up the target via the jump host: `rtmux connect user@target --via bastion`

**Multi-layer jump** (target → bastion2 → bastion1 → local):
```bash
rtmux connect user@bastion1
rtmux connect user@bastion2 --via bastion1
rtmux connect user@target --via bastion1,bastion2
```

**Note**: Jump hosts only need SSH key setup (no tmux). Tmux is only installed on the target host.

Once configured, all commands work transparently with jump hosts:
```bash
rtmux --json exec my-session "hostname" --host target-alias --auto-create
rtmux --json cp ./file.txt ::/remote/path --host target-alias
```

## JSON Output Format

All `--json` outputs include connection context when `--host` is specified:

```json
{
  "host": "alias-from-connections",
  "hostname": "10.0.0.1",
  "username": "admin",
  "session": "my-session",
  "status": "success|ok|created|killed|error",
  ...
}
```

**Error format**:
```json
{"status": "error", "error": "message", "code": 1}
```

**Error codes**: 1=generic, 2=config, 3=connection, 4=auth, 5=tmux, 6=keygen

**Exit codes**: `rtmux exec` propagates the remote command's exit code.

## Guardrails

- **Connection init flow** — when no connection exists: if user provided full info (user + host + password), automate it; otherwise give the command and let the user run it or provide the password later.
- **All commands run inside tmux** — no "direct run" mode exists.
- **Session naming is the agent's responsibility** — pick meaningful names per task.
- **Use `--json` for automation** — output includes host/session context for identification.
- **Use `--base64` for complex commands** — pipes, quotes, `$`, heredocs, multi-line.
- **Use `--auto-create` to simplify** — no separate `rtmux new` needed.
- **Read before write** — inspect remote state before modifying files.
- **Verify after write** — read back after remote file edits.
- **Clean up when done** — kill sessions that are no longer needed.

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| `未找到连接: xxx` | Host alias not in config | `rtmux connections` to list, or `rtmux connect` to add |
| `会话不存在: xxx` | Session not created | Add `--auto-create` to exec |
| `SSH 认证失败` | Key rejected or expired | `rtmux connect user@host` to refresh |
| `命令可能仍在执行中（超时）` | Command took too long | Increase `--timeout` |
| `tmux 安装失败` | Remote can't install tmux | Install manually: `sudo apt install tmux` |

## Resources

- `agent/cli.py` — rtmux CLI with `--json`, `--base64`, `--auto-create`
- `agent/session.py` — tmux session management with exit code capture
- `agent/ssh_engine.py` — SSH connection engine (paramiko, replaces sshpass/plink)
- `agent/file_manager.py` — file copy with `::` prefix and resume support
- `agent/tmux_installer.py` — auto-install tmux from deb packages
- `setup_rtmux.py` — cross-platform environment setup
- `references/usage.md` — detailed usage examples and patterns
