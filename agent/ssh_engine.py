"""SSH 引擎：密钥管理、连接管理（不修改系统 SSH 配置）"""

import subprocess
from pathlib import Path
from typing import Optional, Tuple, List

import paramiko

from .exceptions import (
    AuthError,
    ConnectionError,
    KeyGenerationError,
)
from .utils import is_windows

# agent 独立目录（所有数据隔离在此）
AGENT_DIR: Path = Path.home() / ".remote-ops"
SOCKETS_DIR: Path = AGENT_DIR / "sockets"


class ExecResult:
    """封装远程命令执行结果"""

    def __init__(self, stdout: str, stderr: str, exit_code: int):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def failed(self) -> bool:
        return self.exit_code != 0

    def __repr__(self) -> str:
        return f"ExecResult(exit_code={self.exit_code}, stdout={self.stdout[:50]!r}...)"


# ---------------------------------------------------------------------------
# 密钥管理
# ---------------------------------------------------------------------------

class KeyManager:
    """生成和管理 SSH 密钥对（完全隔离在 ~/.remote-ops/keys/）"""

    KEYS_DIR: Path = Path.home() / ".remote-ops" / "keys"

    @classmethod
    def key_paths(cls, key_name: str) -> Tuple[Path, Path]:
        """返回 (私钥路径, 公钥路径)"""
        private = cls.KEYS_DIR / key_name
        public = cls.KEYS_DIR / f"{key_name}.pub"
        return private, public

    @classmethod
    def key_exists(cls, key_name: str) -> bool:
        """检查私钥文件是否已存在"""
        private, _ = cls.key_paths(key_name)
        return private.exists()

    @classmethod
    def generate_key(cls, key_name: str, passphrase: str = "") -> None:
        """
        使用 ed25519 生成 SSH 密钥对

        密钥文件写入 ~/.agent/keys/{key_name} 和 ~/.agent/keys/{key_name}.pub

        Raises:
            KeyGenerationError: 生成失败时抛出
        """
        private, public = cls.key_paths(key_name)

        # 确保 keys 目录存在（权限 700）
        cls.KEYS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)

        try:
            result = subprocess.run(
                [
                    "ssh-keygen",
                    "-t", "ed25519",
                    "-f", str(private),
                    "-N", passphrase,
                    "-q",  # 静默模式
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                raise KeyGenerationError(
                    f"ssh-keygen 失败: {result.stderr.strip()}"
                )
        except FileNotFoundError:
            raise KeyGenerationError(
                "未找到 ssh-keygen 命令，请确保已安装 OpenSSH"
            )
        except subprocess.TimeoutExpired:
            raise KeyGenerationError("ssh-keygen 超时")

    @classmethod
    def get_public_key(cls, key_name: str) -> str:
        """读取并返回公钥内容"""
        _, public = cls.key_paths(key_name)
        if not public.exists():
            raise KeyGenerationError(f"公钥文件不存在: {public}")
        return public.read_text().strip()

    @classmethod
    def get_private_key_path(cls, key_name: str) -> Path:
        """返回私钥路径"""
        private, _ = cls.key_paths(key_name)
        if not private.exists():
            raise KeyGenerationError(f"私钥文件不存在: {private}")
        return private


# ---------------------------------------------------------------------------
# 公钥上传
# ---------------------------------------------------------------------------

def upload_public_key(
    hostname: str,
    port: int,
    username: str,
    password: str,
    public_key: str,
    proxy_client: Optional[paramiko.SSHClient] = None,
) -> None:
    """
    使用密码认证连接远程主机，将公钥追加到 ~/.ssh/authorized_keys

    Args:
        hostname: 目标主机
        port: SSH 端口
        username: 用户名
        password: 密码
        public_key: 公钥内容
        proxy_client: 跳板机 SSH 客户端（可选）

    Raises:
        AuthError: 密码认证失败
        ConnectionError: 连接失败
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    connect_kwargs = {
        "hostname": hostname,
        "port": port,
        "username": username,
        "password": password,
        "timeout": 30,
        "banner_timeout": 30,
        "auth_timeout": 30,
        "allow_agent": False,
        "look_for_keys": False,
    }

    # 通过跳板机建立隧道
    if proxy_client:
        proxy_transport = proxy_client.get_transport()
        channel = proxy_transport.open_channel(
            "direct-tcpip",
            (hostname, port),
            ("127.0.0.1", 0),
        )
        connect_kwargs["sock"] = channel

    try:
        client.connect(**connect_kwargs)
    except paramiko.AuthenticationException:
        raise AuthError(f"密码认证失败: {username}@{hostname}")
    except Exception as e:
        raise ConnectionError(f"SSH 连接失败: {e}")

    try:
        # 确保 .ssh 目录存在并设置权限
        commands = [
            "mkdir -p ~/.ssh && chmod 700 ~/.ssh",
            f'grep -qF "{public_key}" ~/.ssh/authorized_keys 2>/dev/null || '
            f'echo "{public_key}" >> ~/.ssh/authorized_keys',
            "chmod 600 ~/.ssh/authorized_keys",
        ]
        for cmd in commands:
            stdin, stdout, stderr = client.exec_command(cmd)
            exit_code = stdout.channel.recv_exit_status()
            if exit_code != 0:
                err = stderr.read().decode().strip()
                raise ConnectionError(f"远程命令失败 ({cmd}): {err}")
    finally:
        client.close()


# ---------------------------------------------------------------------------
# SSH 连接引擎
# ---------------------------------------------------------------------------

class SSHEngine:
    """封装 paramiko 进行命令执行与会话管理"""

    def __init__(
        self,
        hostname: str,
        username: str,
        key_path: Optional[str] = None,
        password: Optional[str] = None,
        port: int = 22,
        jump_hosts: Optional[List[dict]] = None,
    ) -> None:
        """
        Args:
            hostname: 目标主机
            username: 用户名
            key_path: 私钥路径
            password: 密码
            port: SSH 端口
            jump_hosts: 跳板机列表，每个元素包含 hostname/username/key_path/password/port
        """
        self.hostname = hostname
        self.username = username
        self.key_path = key_path
        self.password = password
        self.port = port
        self.jump_hosts = jump_hosts or []
        self._client: Optional[paramiko.SSHClient] = None
        self._jump_clients: List[paramiko.SSHClient] = []  # 保持跳板机连接

    def _connect_jump_chain(self) -> Optional[paramiko.SSHClient]:
        """
        建立跳板机链，返回最后一个跳板机的 client

        Returns:
            最后一个跳板机的 SSHClient，无跳板机时返回 None
        """
        if not self.jump_hosts:
            return None

        prev_client = None
        for i, jump in enumerate(self.jump_hosts):
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

            connect_kwargs = {
                "hostname": jump["hostname"],
                "port": jump.get("port", 22),
                "username": jump["username"],
                "timeout": 30,
                "banner_timeout": 30,
                "auth_timeout": 30,
            }

            # 通过上一个跳板机建立隧道
            if prev_client:
                proxy_transport = prev_client.get_transport()
                channel = proxy_transport.open_channel(
                    "direct-tcpip",
                    (jump["hostname"], jump.get("port", 22)),
                    ("127.0.0.1", 0),
                )
                connect_kwargs["sock"] = channel

            # 认证方式
            jump_key = jump.get("key_path")
            jump_password = jump.get("password")
            if jump_key:
                try:
                    pkey = paramiko.Ed25519Key.from_private_key_file(jump_key)
                except paramiko.ssh_exception.SSHException:
                    try:
                        pkey = paramiko.RSAKey.from_private_key_file(jump_key)
                    except Exception:
                        raise AuthError(f"无法加载跳板机私钥: {jump_key}")
                connect_kwargs["pkey"] = pkey
            elif jump_password:
                connect_kwargs["password"] = jump_password
                connect_kwargs["allow_agent"] = False
                connect_kwargs["look_for_keys"] = False
            else:
                raise AuthError(f"跳板机 {jump['hostname']} 需要 key_path 或 password")

            try:
                client.connect(**connect_kwargs)
            except paramiko.AuthenticationException:
                raise AuthError(f"跳板机认证失败: {jump['username']}@{jump['hostname']}")
            except Exception as e:
                raise ConnectionError(f"跳板机连接失败: {e}")

            self._jump_clients.append(client)
            prev_client = client

        return prev_client

    def connect(self) -> None:
        """
        建立 SSH 连接

        优先使用密钥认证，无密钥时回退到密码认证

        Raises:
            AuthError: 认证失败
            ConnectionError: 连接失败
        """
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_kwargs = {
            "hostname": self.hostname,
            "port": self.port,
            "username": self.username,
            "timeout": 30,
            "banner_timeout": 30,
            "auth_timeout": 30,
        }

        # 建立跳板机链
        proxy_client = self._connect_jump_chain()
        if proxy_client:
            proxy_transport = proxy_client.get_transport()
            channel = proxy_transport.open_channel(
                "direct-tcpip",
                (self.hostname, self.port),
                ("127.0.0.1", 0),
            )
            connect_kwargs["sock"] = channel

        if self.key_path:
            try:
                pkey = paramiko.Ed25519Key.from_private_key_file(self.key_path)
            except paramiko.ssh_exception.SSHException:
                try:
                    pkey = paramiko.RSAKey.from_private_key_file(self.key_path)
                except Exception:
                    raise AuthError(f"无法加载私钥: {self.key_path}")
            connect_kwargs["pkey"] = pkey
        elif self.password:
            connect_kwargs["password"] = self.password
            connect_kwargs["allow_agent"] = False
            connect_kwargs["look_for_keys"] = False
        else:
            raise AuthError("必须提供 key_path 或 password")

        try:
            self._client.connect(**connect_kwargs)
        except paramiko.AuthenticationException:
            raise AuthError(
                f"SSH 认证失败: {self.username}@{self.hostname}:{self.port}"
            )
        except Exception as e:
            raise ConnectionError(f"SSH 连接失败: {e}")

    def exec(self, command: str, timeout: int = 30) -> ExecResult:
        """
        在远程主机上执行命令

        Raises:
            ConnectionError: 未连接或执行失败
        """
        if not self._client:
            raise ConnectionError("SSH 未连接，请先调用 connect()")

        try:
            stdin, stdout, stderr = self._client.exec_command(
                command, timeout=timeout
            )
            exit_code = stdout.channel.recv_exit_status()
            return ExecResult(
                stdout=stdout.read().decode('utf-8', errors='replace'),
                stderr=stderr.read().decode('utf-8', errors='replace'),
                exit_code=exit_code,
            )
        except Exception as e:
            raise ConnectionError(f"命令执行失败: {e}")

    def exec_stream(
        self,
        command: str,
        on_stdout=None,
        on_stderr=None,
        timeout: int = 300,
    ) -> int:
        """
        流式执行命令，数据到达时调用回调

        Returns:
            退出码
        """
        if not self._client:
            raise ConnectionError("SSH 未连接")

        transport = self._client.get_transport()
        channel = transport.open_session()
        channel.settimeout(timeout)
        channel.exec_command(command)

        while True:
            if channel.recv_ready():
                data = channel.recv(4096).decode('utf-8', errors='replace')
                if on_stdout:
                    on_stdout(data)
            if channel.recv_stderr_ready():
                data = channel.recv_stderr(4096).decode('utf-8', errors='replace')
                if on_stderr:
                    on_stderr(data)
            if channel.exit_status_ready():
                # 读取剩余数据
                while channel.recv_ready():
                    data = channel.recv(4096).decode('utf-8', errors='replace')
                    if on_stdout:
                        on_stdout(data)
                break

        channel.close()
        return channel.recv_exit_status()

    def invoke_shell(self) -> paramiko.Channel:
        """打开交互式 shell 通道"""
        if not self._client:
            raise ConnectionError("SSH 未连接")
        return self._client.invoke_shell()

    def close(self) -> None:
        """关闭 SSH 连接（包括跳板机）"""
        if self._client:
            self._client.close()
            self._client = None
        # 关闭跳板机连接（反向关闭）
        for client in reversed(self._jump_clients):
            try:
                client.close()
            except Exception:
                pass
        self._jump_clients.clear()

    def __enter__(self) -> 'SSHEngine':
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        self.close()


# ---------------------------------------------------------------------------
# SSH 子进程命令构建（用于 attach 等需要 subprocess 的场景）
# ---------------------------------------------------------------------------

def build_ssh_command(
    hostname: str,
    username: str,
    key_path: str,
    port: int = 22,
    remote_command: Optional[str] = None,
    tty: bool = False,
    jump_hosts: Optional[List[dict]] = None,
) -> list[str]:
    """
    构建完整的 ssh 命令（所有选项通过 -o 显式传入，不依赖 ~/.ssh/config）

    Args:
        hostname: 远程主机
        username: 用户名
        key_path: 私钥路径
        port: SSH 端口
        remote_command: 远程命令（可选）
        tty: 是否分配 TTY（-t 标志）
        jump_hosts: 跳板机列表（可选）

    Returns:
        完整的 ssh 命令列表
    """
    cmd = [
        "ssh",
        "-i", str(key_path),
        "-p", str(port),
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "GlobalKnownHostsFile=/dev/null",
    ]

    # 跳板机（ProxyJump）
    if jump_hosts:
        jump_str = ",".join(
            f"{j['username']}@{j['hostname']}:{j.get('port', 22)}"
            for j in jump_hosts
        )
        cmd.extend(["-J", jump_str])

    # SSH Multiplexing（非 Windows）
    if not is_windows():
        SOCKETS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        socket_path = str(SOCKETS_DIR / f"{username}@{hostname}:{port}")
        cmd.extend([
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={socket_path}",
            "-o", "ControlPersist=10m",
        ])

    if tty:
        cmd.append("-t")

    cmd.append(f"{username}@{hostname}")

    if remote_command:
        cmd.append(remote_command)

    return cmd
