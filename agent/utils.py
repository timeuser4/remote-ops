"""工具函数"""

import re
import socket
import platform
from typing import Tuple


def is_windows() -> bool:
    """判断当前平台是否为 Windows"""
    return platform.system() == "Windows"


def get_local_hostname() -> str:
    """获取本机主机名"""
    return socket.gethostname()


def resolve_ip(hostname: str) -> str:
    """将主机名解析为 IP 地址，失败时返回原始主机名"""
    try:
        return socket.gethostbyname(hostname)
    except socket.gaierror:
        return hostname


def parse_target(target: str) -> Tuple[str, str]:
    """
    解析 'user@host' 格式的目标地址

    Returns:
        (username, hostname) 元组

    Raises:
        ValueError: 格式不正确时抛出
    """
    if "@" not in target:
        raise ValueError("目标地址格式必须为 user@hostname")
    parts = target.split("@", 1)
    username = parts[0].strip()
    hostname = parts[1].strip()
    if not username or not hostname:
        raise ValueError("目标地址格式必须为 user@hostname")
    return username, hostname


def build_key_name(local_hostname: str, remote_username: str, remote_ip: str) -> str:
    """
    构建 SSH 密钥文件名

    格式: {本机名}_{远程用户}_{远程IP}
    点号替换为短横线以避免文件名问题
    """
    safe_hostname = local_hostname.replace(".", "-")
    safe_ip = remote_ip.replace(".", "-")
    return f"{safe_hostname}_{remote_username}_{safe_ip}"


def strip_ansi(text: str) -> str:
    """移除 ANSI 转义序列"""
    ansi_pattern = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
    return ansi_pattern.sub('', text)


def clean_tmux_output(output: str, command: str = "") -> str:
    """
    清理 tmux capture-pane 输出

    策略：找到 marker 行（___AGENT_DONE_xxx___），然后向前找到对应的
    shell 提示符（$ 结尾），提取两者之间的内容作为命令输出。
    回退方案：逐行过滤标记和提示符。
    """
    lines = output.split('\n')
    plain_text = strip_ansi(output)
    plain_lines = [strip_ansi(l) for l in lines]

    # 先用 marker 在全文中定位（处理终端换行截断 marker 的情况）
    # 找最后一个 marker（避免匹配 send-keys 的回显）
    marker_pos = plain_text.rfind('___AGENT_DONE_')
    exit_marker_pos = plain_text.rfind('___AGENT_EXIT_')

    if marker_pos >= 0:
        # 找到 marker 在原文中的位置，反推到行号
        marker_line = plain_text[:marker_pos].count('\n')

        # 从 marker 向前找包含 shell 提示符的行（$ 后跟命令内容）
        # 例如: user@ubuntu:~$ echo ... | base64 -d | bash
        # 提示符可能不在行尾（命令跟在后面）
        prompt_idx = -1
        for i in range(marker_line, -1, -1):
            stripped = plain_lines[i].rstrip()
            # 匹配提示符：行中有 $ 且不是 echo 标记命令
            if '$' in stripped and not stripped.startswith('echo ___AGENT'):
                # 确认是提示符而不是 marker 输出中的 $0___AGENT_EXIT
                if '___AGENT_' not in stripped:
                    prompt_idx = i
                    break

        if prompt_idx >= 0:
            # 提取提示符行之后、marker 之前的行
            raw_output = lines[prompt_idx + 1:]
            filtered = []
            for line in raw_output:
                pl = strip_ansi(line)
                if '___AGENT_DONE_' in pl or '___AGENT_EXIT_' in pl:
                    break
                filtered.append(line)

            # 跳过命令换行续行：如果提示符行被终端截断（长度 >= 78 字符），
            # 说明命令跨行了，需要跳过续行
            prompt_plain = strip_ansi(lines[prompt_idx]).rstrip()
            if len(prompt_plain) >= 78 and filtered:
                # 跳过第一行续行（命令的剩余部分）
                filtered.pop(0)

            # 移除尾部空行
            while filtered and not strip_ansi(filtered[-1]).strip():
                filtered.pop()
            return '\n'.join(filtered)

    # 回退：逐行过滤
    cleaned = []
    for line in lines:
        stripped = strip_ansi(line).strip()
        if '___AGENT_DONE_' in stripped:
            continue
        if '___AGENT_EXIT_' in stripped:
            continue
        if stripped.startswith('echo ___AGENT_DONE_') or stripped.startswith('echo ___AGENT_EXIT_'):
            continue
        cleaned.append(line)

    # 移除前后空行
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()

    return '\n'.join(cleaned)
