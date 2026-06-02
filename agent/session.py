"""通过 SSH 操作远程 tmux 进行会话管理"""

import base64
import time
from typing import Dict, List, Optional

from .ssh_engine import SSHEngine
from .exceptions import TmuxError
from .utils import clean_tmux_output, strip_ansi


REMOTE_TMUX = "~/.local/bin/tmux"


class TmuxSessionManager:
    """远程 tmux 会话管理器"""

    def __init__(self, ssh: SSHEngine) -> None:
        self.ssh = ssh

    def _tmux(self, args: str, timeout: int = 10) -> 'ExecResult':
        """执行远程 tmux 命令"""
        return self.ssh.exec(f"{REMOTE_TMUX} {args}", timeout=timeout)

    # -----------------------------------------------------------------------
    # 会话生命周期
    # -----------------------------------------------------------------------

    def create(self, name: str, start_directory: Optional[str] = None) -> Dict:
        """
        创建新的 tmux 会话

        Args:
            name: 会话名称
            start_directory: 启动目录（可选）

        Returns:
            包含会话信息的字典
        """
        cmd = f"new-session -d -s {name}"
        if start_directory:
            cmd += f" -c {start_directory}"

        result = self._tmux(cmd)
        if result.failed:
            stderr = result.stderr.strip()
            if "already exist" in stderr.lower() or "duplicate session" in stderr.lower():
                raise TmuxError(f"会话已存在: {name}")
            raise TmuxError(f"创建会话失败: {stderr}")

        return {
            "status": "created",
            "name": name,
            "directory": start_directory or "~",
        }

    def list_sessions(self) -> List[Dict]:
        """
        列出所有 tmux 会话

        Returns:
            会话信息列表
        """
        # 用 | 作分隔符（\t 在 send-keys 中会被转义为字面量）
        sep = "|"
        result = self._tmux(
            f'list-sessions -F "#{{session_name}}{sep}#{{session_created}}{sep}#{{session_windows}}" 2>/dev/null || true'
        )

        sessions = []
        for line in result.stdout.strip().split('\n'):
            line = line.strip()
            if not line:
                continue
            parts = line.split(sep)
            if len(parts) >= 3:
                sessions.append({
                    "name": parts[0],
                    "created": parts[1],
                    "windows": int(parts[2]) if parts[2].isdigit() else 1,
                })
            elif len(parts) >= 1:
                sessions.append({
                    "name": parts[0],
                    "created": "",
                    "windows": 1,
                })

        return sessions

    def has_session(self, name: str) -> bool:
        """检查会话是否存在"""
        result = self._tmux(f"has-session -t {name} 2>/dev/null")
        return result.ok

    def kill(self, name: str) -> Dict:
        """
        关闭 tmux 会话

        Returns:
            操作结果
        """
        if not self.has_session(name):
            raise TmuxError(f"会话不存在: {name}")

        result = self._tmux(f"kill-session -t {name}")
        if result.failed:
            raise TmuxError(f"关闭会话失败: {result.stderr.strip()}")

        return {"status": "killed", "name": name}

    # -----------------------------------------------------------------------
    # 命令执行
    # -----------------------------------------------------------------------

    def exec_command(
        self,
        session_name: str,
        command: str,
        timeout: int = 30,
        auto_create: bool = False,
        use_base64: bool = False,
    ) -> Dict:
        """
        在 tmux 会话中执行命令

        流程：
        1. send-keys 发送命令
        2. send-keys 捕获 $? 到变量
        3. send-keys 发送完成标记
        4. 轮询 capture-pane 检测标记
        5. 提取输出和退出码

        Args:
            session_name: tmux 会话名称
            command: 要执行的命令
            timeout: 超时秒数
            auto_create: 会话不存在时自动创建
            use_base64: 使用 base64 编码传输命令，避免转义问题

        Returns:
            {"status": "success", "output": "...", "exit_code": 0}
        """
        if not self.has_session(session_name):
            if auto_create:
                self.create(session_name)
            else:
                return {"status": "error", "error": f"会话不存在: {session_name}", "exit_code": 1}

        # 生成唯一标记
        ts = int(time.time_ns())
        marker = f"___AGENT_DONE_{ts}___"
        exit_marker = f"___AGENT_EXIT_{ts}___"

        # 发送命令
        if use_base64:
            # base64 编码后只有 A-Z a-z 0-9 + / =，不需要任何转义
            encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")
            tmux_command = f"echo {encoded} | base64 -d | bash"
        else:
            tmux_command = command
        self._tmux(f'send-keys -t {session_name} "{tmux_command}" Enter')

        # 捕获退出码并输出标记
        self._tmux(
            f'send-keys -t {session_name} '
            f'"echo {exit_marker}$?{exit_marker}; echo {marker}" Enter'
        )

        # 轮询检测标记
        start_time = time.time()
        poll_interval = 0.3
        max_polls = int(timeout / poll_interval)

        for _ in range(max_polls):
            time.sleep(poll_interval)

            result = self._tmux(
                f"capture-pane -t {session_name} -p -S -32768",
                timeout=10,
            )

            output = result.stdout
            plain_output = strip_ansi(output)

            if marker in plain_output:
                # 找到标记，提取退出码
                exit_code = self._extract_exit_code(plain_output, exit_marker)
                cleaned = clean_tmux_output(output, tmux_command)
                return {
                    "status": "success",
                    "output": cleaned,
                    "exit_code": exit_code,
                }

            elapsed = time.time() - start_time
            if elapsed >= timeout:
                break

        # 超时，返回当前可用输出
        result = self._tmux(f"capture-pane -t {session_name} -p -S -100")
        cleaned = clean_tmux_output(result.stdout, tmux_command)
        return {
            "status": "success",
            "output": cleaned,
            "exit_code": -1,
            "note": "命令可能仍在执行中（超时）",
        }

    @staticmethod
    def _extract_exit_code(text: str, exit_marker: str) -> int:
        """从 capture-pane 输出中提取退出码"""
        import re
        pattern = re.escape(exit_marker) + r"(\d+)" + re.escape(exit_marker)
        matches = re.findall(pattern, text)
        if matches:
            try:
                return int(matches[-1])
            except ValueError:
                pass
        return -1

    def send_keys(self, session_name: str, keys: str) -> None:
        """向会话发送按键"""
        self._tmux(f'send-keys -t {session_name} "{keys}"')

    def capture_history(self, session_name: str, lines: int = 0) -> str:
        """
        捕获会话的终端输出历史

        Args:
            session_name: 会话名称
            lines: 捕获行数，0 表示全部

        Returns:
            终端输出文本
        """
        if lines > 0:
            result = self._tmux(f"capture-pane -t {session_name} -p -S -{lines}")
        else:
            result = self._tmux(f"capture-pane -t {session_name} -p -S -")
        return strip_ansi(result.stdout)

    # -----------------------------------------------------------------------
    # Attach
    # -----------------------------------------------------------------------

    def attach_command(self, session_name: str, hostname: str, username: str, key_path: str, port: int = 22) -> str:
        """
        生成 attach 的 SSH 命令字符串

        用于 subprocess 调用，将终端转发到远程 tmux
        """
        tmux_cmd = f"{REMOTE_TMUX} attach-session -t {session_name}"
        ssh_cmd = (
            f"ssh -t -o StrictHostKeyChecking=no "
            f"-i {key_path} -p {port} "
            f"{username}@{hostname} "
            f"'{tmux_cmd}'"
        )
        return ssh_cmd
