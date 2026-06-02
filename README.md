# remote-ops

跨平台远程主机操作技能包，适用于 Claude Code、Codex CLI 和 OpenCode。

基于 **rtmux**（SSH + tmux）引擎，替代 sshpass 和 plink。所有命令在远程 tmux 会话中执行，终端历史、工作目录、环境变量在断开后保持。

## 特性

- **会话持久化**：基于远程 tmux，会话在 SSH 断开后依然保持
- **SSH Key 自动管理**：首次连接用密码，之后自动生成密钥免密登录
- **tmux 自动安装**：远程主机无 tmux 时自动从 deb 包提取安装
- **安全命令传输**：`--base64` 避免所有 shell 转义问题
- **文件复制**：`cp` 命令（类似 scp），支持断点续传
- **JSON 输出**：`--json` 含连接上下文（host/hostname/username/session）
- **跨平台客户端**：macOS、Linux、Windows（paramiko SSH）

## 安装

```bash
python3 setup_rtmux.py
```

自动完成：创建 venv → 安装 paramiko → 安装 rtmux CLI → 安装 skill 文件。

连接远程服务器：

```bash
rtmux connect user@host --port 6000
```

通过跳板机连接：

```bash
# 先设置跳板机
rtmux connect user@bastion

# 再通过跳板机设置目标主机
rtmux connect user@target --via bastion

# 多层跳板机
rtmux connect user@target --via bastion1,bastion2
```

## 使用

### 命令执行

```bash
# 基础执行
rtmux --json exec my-session "hostname && df -h" --host server1

# 自动创建会话
rtmux --json exec my-session "whoami" --host server1 --auto-create

# 复杂命令（base64 安全传输）
rtmux --json exec my-session 'echo "hello $USER" | grep hello' --host server1 --base64
```

### 文件复制

```bash
# 上传（:: 标识远程路径）
rtmux --json cp ./local/file.txt ::/remote/path --host server1

# 下载
rtmux --json cp ::/remote/file.txt ./local/path --host server1

# 递归 + 断点续传
rtmux --json cp ./dir ::/remote/path -r --host server1 --resume
```

### 会话管理

```bash
rtmux --json list --host server1           # 列出会话
rtmux --json capture my-session --host s1  # 捕获终端历史
rtmux --json kill my-session --host s1     # 关闭会话
```

### 文件操作

```bash
rtmux --json ls /remote/path --host server1       # 列出文件
rtmux --json rm /remote/path --host server1       # 删除文件
rtmux --json proxy-dl https://url /path --host s1 # 代理下载
```

## 技术栈

| 组件 | 技术 | 说明 |
|------|------|------|
| SSH | paramiko | 替代 sshpass/plink |
| 会话 | tmux | 远程持久会话 |
| 文件传输 | SFTP | paramiko 内置，二进制协议 |
| CLI | click | 跨平台命令行 |
| 密钥 | ed25519 | ssh-keygen 自动生成 |

## 命令参考

| 命令 | 说明 |
|------|------|
| `rtmux connect <user@host>` | 设置连接（自动 SSH key + tmux 安装） |
| `rtmux connect <user@host> --via <alias>` | 通过跳板机设置连接 |
| `rtmux connections` | 列出已保存连接 |
| `rtmux disconnect <alias>` | 移除连接 |
| `rtmux --json exec <session> <cmd> --host <alias>` | 执行命令（`--base64` `--auto-create`） |
| `rtmux --json cp <src> <dst> --host <alias>` | 复制文件（`::` 标识远程，`--resume`） |
| `rtmux --json list --host <alias>` | 列出会话 |
| `rtmux --json capture <session> --host <alias>` | 捕获终端历史 |
| `rtmux --json kill <session> --host <alias>` | 关闭会话 |
| `rtmux --json ls <path> --host <alias>` | 列出远程文件 |
| `rtmux --json rm <path> --host <alias>` | 删除远程文件 |
| `rtmux --json proxy-dl <url> <path> --host <alias>` | 代理下载 |

## License

MIT
