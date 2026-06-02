# rtmux Usage Reference

## Installation

```bash
# Setup script (creates venv, installs paramiko + rtmux)
python3 setup_rtmux.py

# Or manual install
pip install .
```

## Connection Setup

### First-time setup

```bash
rtmux connect user@hostname --port 6000
# Interactive password prompt, then:
# - Generates SSH key (~/.remote-ops/keys/{key_name})
# - Uploads public key to remote
# - Saves connection config (~/.remote-ops/connections.json)
# - Auto-installs tmux on remote if missing
```

### Non-interactive setup

```bash
export SSHPASS="your-password"
rtmux connect user@hostname --password-env SSHPASS
unset SSHPASS
```

### Jump host setup

When the target host is only accessible through a bastion/jump host:

```bash
# Step 1: Set up the jump host (no tmux installed)
rtmux connect user@bastion

# Step 2: Set up the target via the jump host
rtmux connect user@target --via bastion

# Multi-layer jump (target -> bastion2 -> bastion1 -> local)
rtmux connect user@bastion1
rtmux connect user@bastion2 --via bastion1
rtmux connect user@target --via bastion1,bastion2
```

**Note**: Jump hosts only need SSH key setup (no tmux). Tmux is only installed on the target host.

### List / remove connections

```bash
rtmux --json connections
rtmux disconnect <alias>
rtmux disconnect <alias> --remove-key
```

## Command Execution

### Basic

```bash
rtmux --json exec my-session "hostname && uname -a" --host server1
```

### Auto-create session

```bash
rtmux --json exec my-session "pwd" --host server1 --auto-create
```

### Custom timeout

```bash
rtmux --json exec my-session "slow-task" --host server1 --timeout 120
```

### Exit code propagation

```bash
rtmux --json exec my-session "test -f /etc/passwd" --host server1
echo $?  # 0

rtmux --json exec my-session "test -f /nonexistent" --host server1
echo $?  # 1
```

### Base64 mode

Use `--base64` for commands with special characters. Encoded locally, decoded on remote.

```bash
# Pipes, quotes, $variables
rtmux --json exec s1 'echo "hello $USER" | grep hello' --host server1 --base64

# Multi-line scripts
rtmux --json exec s1 'for f in /tmp/*.log; do
  echo "=== $f ==="
  tail -5 "$f"
done' --host server1 --base64

# Heredoc / YAML / JSON
rtmux --json exec s1 'cat > /tmp/config.yaml << EOF
key: "value with spaces"
nested:
  list: [1, 2, 3]
EOF' --host server1 --base64

# Command substitution
rtmux --json exec s1 'echo "Disk: $(df -h / | tail -1 | awk "{print \$5}")"' --host server1 --base64
```

## File Copy (cp)

类似 scp，用 `::` 前缀标识远程路径（双冒号避免 Windows 盘符冲突）：

```bash
# Upload
rtmux --json cp ./config.yaml ::/opt/app/config.yaml --host server1
rtmux --json cp ./dist ::/opt/app/dist -r --host server1

# Download
rtmux --json cp ::/var/log/syslog ./logs/ --host server1

# Resume (断点续传)
rtmux --json cp ./big-file.tar.gz ::/remote/path --host server1 --resume
rtmux --json cp ::/remote/big-file.tar.gz ./local/path --host server1 --resume
```

## Other File Operations

```bash
# List
rtmux --json ls /opt/app --host server1
rtmux --json ls /opt/app -l --host server1  # long format

# Delete
rtmux --json rm /tmp/old-file.txt --host server1
rtmux --json rm /tmp/old-dir --host server1  # JSON mode auto-skips confirmation

# Proxy download
rtmux --json proxy-dl https://example.com/data.tar.gz /tmp/data.tar.gz --host server1
```

## Session Management

```bash
# List sessions
rtmux --json list --host server1

# Kill session
rtmux --json kill my-session --host server1

# Capture terminal history
rtmux --json capture my-session --host server1
rtmux --json capture my-session --host server1 --lines 200

# Create session explicitly
rtmux --json new my-session --host server1
```

## JSON Output

### With connection context

All `--json` outputs with `--host` include connection info:

```json
{
  "host": "server1",
  "hostname": "10.0.0.1",
  "username": "admin",
  "session": "my-session",
  "status": "success",
  "output": "command output",
  "exit_code": 0
}
```

### Success responses

```json
// exec
{"host":"...","hostname":"...","username":"...","session":"...","status":"success","output":"...","exit_code":0}

// list
{"host":"...","status":"ok","sessions":[{"name":"...","windows":1,"created":"..."}]}

// kill
{"host":"...","session":"...","status":"killed","name":"...","message":"会话已关闭: ..."}

// ls
{"host":"...","status":"ok","path":"/remote","files":[{"name":"...","is_dir":false,"size":1024,"modified":"..."}]}

// upload/download
{"host":"...","status":"ok","files":3,"size":10240,"message":"已上传 3 个文件，共 10.0 KB"}

// capture
{"host":"...","session":"...","status":"ok","output":"terminal output..."}
```

### Error response

```json
{"status":"error","error":"会话不存在: xxx","code":5}
```

### Error codes

| Code | Type | Meaning |
|------|------|---------|
| 1 | Generic | Unspecified error |
| 2 | ConfigError | Connection not found, config corrupted |
| 3 | ConnectionError | Network failure, host unreachable |
| 4 | AuthError | SSH authentication failed |
| 5 | TmuxError | Session not found, tmux install failed |
| 6 | KeyGenerationError | ssh-keygen failed |

## Agent Integration

```python
import subprocess, json

def rtmux(session, command, host, base64=False, timeout=30):
    """Execute a command in a remote tmux session."""
    cmd = ["rtmux", "--json", "exec", session, command,
           "--host", host, "--timeout", str(timeout), "--auto-create"]
    if base64:
        cmd.append("--base64")
    result = subprocess.run(cmd, capture_output=True, text=True)
    data = json.loads(result.stdout)
    if data.get("status") == "error":
        raise RuntimeError(f"rtmux error: {data['error']} (code {data.get('code')})")
    return data

def rtmux_cp(src, dst, host, recursive=False, resume=False):
    """Copy file to/from remote (like scp). Use :: prefix for remote paths."""
    cmd = ["rtmux", "--json", "cp", src, dst, "--host", host]
    if recursive:
        cmd.append("-r")
    if resume:
        cmd.append("--resume")
    result = subprocess.run(cmd, capture_output=True, text=True)
    return json.loads(result.stdout)

# Usage
r = rtmux("agent-my-task", "df -h", "server1")
print(r["output"])
print(f"Host: {r['hostname']}, Exit: {r['exit_code']}")

# File copy (:: prefix for remote paths)
rtmux_cp("./local.txt", "::/remote/path", "server1")  # upload
rtmux_cp("::/remote/file.txt", "./local", "server1")   # download
```
