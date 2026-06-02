"""CLI 命令定义"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
import paramiko

from .config import AgentConfig
from .exceptions import AgentError, ConfigError
from .file_manager import FileManager
from .session import TmuxSessionManager
from .ssh_engine import (
    KeyManager,
    SSHEngine,
    build_ssh_command,
    upload_public_key,
)
from .tmux_installer import TmuxInstaller
from .utils import (
    build_key_name,
    get_local_hostname,
    parse_target,
    resolve_ip,
)

REMOTE_TMUX = "~/.local/bin/tmux"


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _is_json(ctx: click.Context) -> bool:
    """检查是否启用 JSON 输出模式"""
    return ctx.obj.get("json", False) if ctx.obj else False


def _conn_ctx(ctx: click.Context, host: str = "", session: str = "") -> dict:
    """构建连接上下文，用于 JSON 输出"""
    info = {}
    if host:
        info["host"] = host
        conn = AgentConfig.get_connection(host)
        if conn:
            info["hostname"] = conn.get("hostname", "")
            info["username"] = conn.get("username", "")
    if session:
        info["session"] = session
    return info


def _output(data: dict, ctx: click.Context) -> None:
    """统一输出：JSON 或人类可读"""
    if _is_json(ctx):
        # 注入连接上下文
        if ctx.obj and "_conn" in ctx.obj:
            data = {**ctx.obj["_conn"], **data}
        click.echo(json.dumps(data, ensure_ascii=False))
    else:
        # 人类可读模式下，根据数据类型格式化
        status = data.get("status", "")
        if status == "success":
            output = data.get("output", "")
            if output:
                click.echo(output, nl=False)
                if not output.endswith('\n'):
                    click.echo()
        elif status in ("created", "killed", "ok"):
            click.echo(f"✅ {data.get('message', status)}")
        elif status == "error":
            click.echo(f"错误: {data.get('error', '')}", err=True)


def _error(msg: str, ctx: click.Context, code: int = 1) -> None:
    """统一错误输出"""
    if _is_json(ctx):
        click.echo(json.dumps({"status": "error", "error": msg, "code": code}, ensure_ascii=False))
    else:
        click.echo(f"错误: {msg}", err=True)
    sys.exit(code)


ERROR_CODES = {
    "ConfigError": 2,
    "ConnectionError": 3,
    "AuthError": 4,
    "TmuxError": 5,
    "KeyGenerationError": 6,
}


def _handle_error(e: AgentError, ctx: click.Context) -> None:
    """处理 AgentError，输出结构化错误"""
    code = ERROR_CODES.get(type(e).__name__, 1)
    _error(str(e), ctx, code)


def _resolve_connection(host_alias: str) -> dict:
    """从配置中获取连接信息"""
    conn = AgentConfig.get_connection(host_alias)
    if not conn:
        raise ConfigError(f"未找到连接: {host_alias}，运行 'rtmux connections' 查看已保存连接")
    return conn


def _build_proxy_client(jump_configs: list) -> paramiko.SSHClient:
    """
    根据跳板机配置列表，建立跳板机隧道链

    返回最后一个跳板机的 client，用于后续隧道连接
    """
    prev_client = None
    for jump in jump_configs:
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

        # 密钥认证
        key_path = jump.get("key_path")
        if key_path:
            try:
                pkey = paramiko.Ed25519Key.from_private_key_file(key_path)
            except paramiko.ssh_exception.SSHException:
                pkey = paramiko.RSAKey.from_private_key_file(key_path)
            connect_kwargs["pkey"] = pkey
        else:
            raise ConfigError(f"跳板机 {jump['hostname']} 缺少 key_path")

        try:
            client.connect(**connect_kwargs)
        except Exception as e:
            # 关闭已建立的连接
            if prev_client:
                prev_client.close()
            raise ConnectionError(f"跳板机连接失败: {e}")

        prev_client = client

    return prev_client


def _resolve_jump_hosts(host_alias: str) -> list:
    """
    解析连接的跳板机链，返回跳板机配置列表

    从连接配置的 via 字段递归解析
    """
    conn = _resolve_connection(host_alias)
    via = conn.get("via")
    if not via:
        return []

    jump_aliases = [j.strip() for j in via.split(",")]
    jump_configs = []
    for alias in jump_aliases:
        jump_conn = _resolve_connection(alias)
        jump_configs.append({
            "hostname": jump_conn["hostname"],
            "username": jump_conn["username"],
            "key_path": jump_conn["key_path"],
            "port": jump_conn.get("port", 22),
        })

    return jump_configs


def _get_engine(host_alias: str) -> SSHEngine:
    """根据别名创建 SSHEngine（自动解析跳板机）"""
    conn = _resolve_connection(host_alias)
    jump_hosts = _resolve_jump_hosts(host_alias)
    return SSHEngine(
        conn["hostname"], conn["username"],
        key_path=conn["key_path"], port=conn.get("port", 22),
        jump_hosts=jump_hosts,
    )


def _print_sessions(sessions: list) -> None:
    """格式化打印会话列表"""
    if not sessions:
        click.echo("无会话")
        return

    click.echo(f"{'名称':<20} {'窗口数':<10} {'创建时间':<20}")
    click.echo("-" * 50)
    for s in sessions:
        name = s.get("name", "")
        windows = s.get("windows", 1)
        created = s.get("created", "")
        click.echo(f"{name:<20} {windows:<10} {created:<20}")


def _print_connections(connections: list) -> None:
    """格式化打印连接列表"""
    if not connections:
        click.echo("无已保存连接\n运行 'rtmux connect user@host' 添加连接")
        return

    has_via = any(c.get("via") for c in connections)
    if has_via:
        click.echo(f"{'别名':<35} {'用户名':<12} {'主机':<20} {'端口':<6} {'跳板机'}")
        click.echo("-" * 90)
        for c in connections:
            alias = c.get("alias", "")
            username = c.get("username", "")
            hostname = c.get("hostname", "")
            port = c.get("port", 22)
            via = c.get("via", "")
            click.echo(f"{alias:<35} {username:<12} {hostname:<20} {port:<6} {via}")
    else:
        click.echo(f"{'别名':<35} {'用户名':<12} {'主机':<20} {'端口':<6}")
        click.echo("-" * 75)
        for c in connections:
            alias = c.get("alias", "")
            username = c.get("username", "")
            hostname = c.get("hostname", "")
            port = c.get("port", 22)
            click.echo(f"{alias:<35} {username:<12} {hostname:<20} {port:<6}")


# ---------------------------------------------------------------------------
# CLI 组
# ---------------------------------------------------------------------------

@click.group()
@click.option("--json", "json_output", is_flag=True, help="以 JSON 格式输出")
@click.pass_context
def cli(ctx, json_output):
    """rtmux - 基于 SSH/tmux 的远程终端复用器，专为 AI Agent 设计"""
    ctx.ensure_object(dict)
    ctx.obj["json"] = json_output


# ---------------------------------------------------------------------------
# connect 命令
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("target")
@click.option("--password", "-p", default=None, help="一次性密码（交互式输入，不推荐用于 agent）")
@click.option("--password-env", default=None, help="从环境变量读取密码")
@click.option("--port", default=22, help="SSH 端口")
@click.option("--via", default=None, help="跳板机别名（多个用逗号分隔）")
@click.option("--force", is_flag=True, help="强制重新生成密钥")
@click.pass_context
def connect(ctx, target: str, password: str, password_env: str, port: int, via: str, force: bool):
    """
    设置到远程主机的连接

    TARGET 格式: user@hostname

    跳板机示例:

        rtmux connect user@target --via bastion

        rtmux connect user@target --via bastion1,bastion2
    """
    try:
        # 解析密码来源
        if password_env:
            import os
            password = os.environ.get(password_env)
            if not password:
                _error(f"环境变量 {password_env} 未设置", ctx, 2)
        elif not password:
            password = click.prompt("密码", hide_input=True)

        # 解析目标
        username, hostname = parse_target(target)
        ip = resolve_ip(hostname)
        local_host = get_local_hostname()
        key_name = build_key_name(local_host, username, ip)

        if not _is_json(ctx):
            click.echo(f"正在设置连接: {username}@{hostname} ({ip})")
            click.echo(f"密钥名称: {key_name}")

        # 解析跳板机链
        jump_aliases = [j.strip() for j in via.split(",")] if via else []
        jump_configs = []

        # 初始化跳板机（只上传 key，不安装 tmux）
        for jump_alias in jump_aliases:
            if not _is_json(ctx):
                click.echo(f"\n  正在初始化跳板机: {jump_alias}")

            # 检查跳板机是否已配置
            existing = AgentConfig.get_connection(jump_alias)
            if existing and KeyManager.key_exists(existing.get("key_path", "").split("/")[-1]):
                if not _is_json(ctx):
                    click.echo(f"    跳板机 {jump_alias} 已配置，跳过")
                jump_configs.append({
                    "hostname": existing["hostname"],
                    "username": existing["username"],
                    "key_path": existing["key_path"],
                    "port": existing.get("port", 22),
                })
                continue

            # 跳板机未配置，需要用户提供信息
            if not _is_json(ctx):
                click.echo(f"    跳板机 {jump_alias} 未配置")
                click.echo(f"    请先运行: rtmux connect user@{jump_alias}")
            _error(f"跳板机 {jump_alias} 未配置，请先初始化", ctx, 2)

        # 生成目标主机密钥
        if KeyManager.key_exists(key_name):
            if force:
                if not _is_json(ctx):
                    click.echo("  密钥已存在，使用 --force 覆盖")
                private_path, _ = KeyManager.key_paths(key_name)
                private_path.unlink(missing_ok=True)
                pub_path = private_path.with_suffix(".pub")
                pub_path.unlink(missing_ok=True)
            else:
                if not _is_json(ctx):
                    click.echo("  密钥已存在，使用已有密钥")

        if not KeyManager.key_exists(key_name):
            if not _is_json(ctx):
                click.echo("  正在生成 SSH 密钥...")
            KeyManager.generate_key(key_name)

        public_key = KeyManager.get_public_key(key_name)
        private_path = KeyManager.get_private_key_path(key_name)

        # 上传公钥到目标主机（通过跳板机）
        if not _is_json(ctx):
            click.echo("  正在上传公钥到远程主机...")

        if jump_configs:
            # 通过跳板机上传：先连接跳板机，再隧道到目标
            proxy_client = _build_proxy_client(jump_configs)
            try:
                upload_public_key(hostname, port, username, password, public_key, proxy_client=proxy_client)
            finally:
                proxy_client.close()
        else:
            upload_public_key(hostname, port, username, password, public_key)

        # 保存连接配置
        AgentConfig.save_connection(
            host_alias=key_name,
            hostname=hostname,
            username=username,
            ip_address=ip,
            key_path=str(private_path),
            port=port,
            via=via,
        )

        # 验证密钥认证（通过跳板机）
        if not _is_json(ctx):
            click.echo("  正在验证密钥认证...")
        with SSHEngine(hostname, username, key_path=str(private_path), port=port, jump_hosts=jump_configs) as ssh:
            result = ssh.exec("echo 'SSH key auth OK'")
            if result.failed and not _is_json(ctx):
                click.echo("  ⚠ 密钥认证验证失败", err=True)

        # 安装 tmux（只有目标主机需要）
        if not _is_json(ctx):
            click.echo("  正在检查 tmux...")
        with SSHEngine(hostname, username, key_path=str(private_path), port=port, jump_hosts=jump_configs) as ssh:
            installer = TmuxInstaller(ssh)
            installer.ensure_tmux()

        data = {
            "status": "ok",
            "alias": key_name,
            "hostname": hostname,
            "username": username,
            "port": port,
            "message": f"连接设置完成: {key_name}",
        }
        if via:
            data["via"] = via
        _output(data, ctx)

        if not _is_json(ctx):
            click.echo("")
            click.echo("使用示例:")
            click.echo(f"  rtmux new my-session --host {key_name}")
            click.echo(f"  rtmux exec my-session 'ls -la' --host {key_name}")
            click.echo(f"  rtmux list --host {key_name}")

    except AgentError as e:
        _handle_error(e, ctx)


# ---------------------------------------------------------------------------
# new 命令
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("session_name")
@click.option("--host", "-h", required=True, help="主机别名")
@click.option("--directory", "-d", help="启动目录")
@click.pass_context
def new(ctx, session_name: str, host: str, directory: Optional[str]):
    """创建新的 tmux 会话"""
    try:
        ctx.obj["_conn"] = _conn_ctx(ctx, host, session_name)
        with _get_engine(host) as ssh:
            mgr = TmuxSessionManager(ssh)
            result = mgr.create(session_name, start_directory=directory)
            _output({"status": "created", "name": session_name, "message": f"会话已创建: {session_name}"}, ctx)

    except AgentError as e:
        _handle_error(e, ctx)


# ---------------------------------------------------------------------------
# list 命令
# ---------------------------------------------------------------------------

@cli.command("list")
@click.option("--host", "-h", required=True, help="主机别名")
@click.pass_context
def list_sessions(ctx, host: str):
    """列出远程主机上的 tmux 会话"""
    try:
        ctx.obj["_conn"] = _conn_ctx(ctx, host)
        with _get_engine(host) as ssh:
            mgr = TmuxSessionManager(ssh)
            sessions = mgr.list_sessions()
            if _is_json(ctx):
                _output({"status": "ok", "sessions": sessions}, ctx)
            else:
                _print_sessions(sessions)

    except AgentError as e:
        _handle_error(e, ctx)


# ---------------------------------------------------------------------------
# exec 命令
# ---------------------------------------------------------------------------

@cli.command("exec")
@click.argument("session_name")
@click.argument("command")
@click.option("--host", "-h", required=True, help="主机别名")
@click.option("--timeout", "-t", default=30, help="超时时间（秒）")
@click.option("--auto-create", is_flag=True, help="会话不存在时自动创建")
@click.option("--base64", "use_base64", is_flag=True, help="使用 base64 编码传输命令，避免转义问题")
@click.pass_context
def exec_command(ctx, session_name: str, command: str, host: str, timeout: int, auto_create: bool, use_base64: bool):
    """在 tmux 会话中执行命令

    使用 --base64 可安全传输包含特殊字符的复杂命令（管道、引号、$变量等）。
    """
    try:
        ctx.obj["_conn"] = _conn_ctx(ctx, host, session_name)
        with _get_engine(host) as ssh:
            mgr = TmuxSessionManager(ssh)
            result = mgr.exec_command(
                session_name, command,
                timeout=timeout,
                auto_create=auto_create,
                use_base64=use_base64,
            )

            if result.get("status") == "error":
                _error(result.get("error", "执行失败"), ctx, 1)

            exit_code = result.get("exit_code", -1)
            _output(result, ctx)
            sys.exit(exit_code if exit_code >= 0 else 1)

    except AgentError as e:
        _handle_error(e, ctx)


# ---------------------------------------------------------------------------
# kill 命令
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("session_name")
@click.option("--host", "-h", required=True, help="主机别名")
@click.pass_context
def kill(ctx, session_name: str, host: str):
    """关闭 tmux 会话"""
    try:
        ctx.obj["_conn"] = _conn_ctx(ctx, host, session_name)
        with _get_engine(host) as ssh:
            mgr = TmuxSessionManager(ssh)
            result = mgr.kill(session_name)
            _output({"status": "killed", "name": session_name, "message": f"会话已关闭: {session_name}"}, ctx)

    except AgentError as e:
        _handle_error(e, ctx)


# ---------------------------------------------------------------------------
# attach 命令
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("session_name")
@click.option("--host", "-h", required=True, help="主机别名")
def attach(session_name: str, host: str):
    """附加到远程 tmux 会话"""
    try:
        conn = _resolve_connection(host)
        jump_hosts = _resolve_jump_hosts(host)
        tmux_cmd = f"{REMOTE_TMUX} attach-session -t {session_name}"

        # 构建完整的 SSH 命令（所有选项显式传入）
        ssh_cmd = build_ssh_command(
            hostname=conn["hostname"],
            username=conn["username"],
            key_path=conn["key_path"],
            port=conn.get("port", 22),
            remote_command=tmux_cmd,
            tty=True,
            jump_hosts=jump_hosts,
        )

        click.echo(f"正在连接到 {session_name}...")
        result = subprocess.call(ssh_cmd)
        sys.exit(result)

    except AgentError as e:
        click.echo(f"错误: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# connections 命令
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
def connections(ctx):
    """列出所有已保存的连接"""
    try:
        conns = AgentConfig.list_connections()
        if _is_json(ctx):
            _output({"status": "ok", "connections": conns}, ctx)
        else:
            _print_connections(conns)

    except AgentError as e:
        _handle_error(e, ctx)


# ---------------------------------------------------------------------------
# disconnect 命令
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("alias")
@click.option("--remove-key", is_flag=True, help="同时删除 SSH 密钥")
@click.pass_context
def disconnect(ctx, alias: str, remove_key: bool):
    """移除已保存的连接"""
    try:
        conn = AgentConfig.get_connection(alias)
        if not conn:
            _error(f"未找到连接: {alias}", ctx, 2)

        # 移除配置
        AgentConfig.remove_connection(alias)

        # 可选：删除密钥
        if remove_key:
            key_path = conn.get("key_path")
            if key_path:
                from pathlib import Path
                p = Path(key_path)
                p.unlink(missing_ok=True)
                p.with_suffix(".pub").unlink(missing_ok=True)

        _output({"status": "ok", "alias": alias, "message": f"已移除连接: {alias}"}, ctx)

    except AgentError as e:
        _handle_error(e, ctx)


# ---------------------------------------------------------------------------
# capture 命令
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("session_name")
@click.option("--host", "-h", required=True, help="主机别名")
@click.option("--lines", "-n", default=0, help="捕获行数，0 表示全部")
@click.pass_context
def capture(ctx, session_name: str, host: str, lines: int):
    """捕获 tmux 会话的终端输出历史"""
    try:
        ctx.obj["_conn"] = _conn_ctx(ctx, host, session_name)
        with _get_engine(host) as ssh:
            mgr = TmuxSessionManager(ssh)
            output = mgr.capture_history(session_name, lines=lines)
            if _is_json(ctx):
                _output({"status": "ok", "session": session_name, "output": output}, ctx)
            else:
                click.echo(output, nl=False)
                if output and not output.endswith('\n'):
                    click.echo()

    except AgentError as e:
        _handle_error(e, ctx)


# ===========================================================================
# 文件操作命令
# ===========================================================================

def _format_size(size: int) -> str:
    """格式化文件大小"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.2f} GB"


# ---------------------------------------------------------------------------
# cp 命令（类似 scp）
# ---------------------------------------------------------------------------

@cli.command("cp")
@click.argument("src")
@click.argument("dst")
@click.option("--host", "-h", required=True, help="主机别名")
@click.option("--recursive", "-r", is_flag=True, help="递归复制目录")
@click.option("--resume", is_flag=True, help="断点续传")
@click.option("--quiet", "-q", is_flag=True, help="不显示进度")
@click.pass_context
def cp(ctx, src: str, dst: str, host: str, recursive: bool, resume: bool, quiet: bool):
    """复制文件（类似 scp）

    用 :: 前缀标识远程路径（双冒号避免 Windows 盘符冲突）：

    \b
    上传: rtmux cp ./local/file.txt ::/remote/path --host server1
    下载: rtmux cp ::/remote/file.txt ./local/path --host server1
    续传: rtmux cp ./big-file.tar.gz ::/remote/path --host server1 --resume
    """
    try:
        ctx.obj["_conn"] = _conn_ctx(ctx, host)
        with _get_engine(host) as ssh:
            fm = FileManager(ssh)
            result = fm.cp(src, dst, recursive=recursive, resume=resume,
                           show_progress=not quiet and not _is_json(ctx))
            _output(result, ctx)

    except (AgentError, FileNotFoundError, ValueError) as e:
        _handle_error(e, ctx) if isinstance(e, AgentError) else _error(str(e), ctx)


# ---------------------------------------------------------------------------
# ls 命令
# ---------------------------------------------------------------------------

@cli.command("ls")
@click.argument("remote_path", default=".")
@click.option("--host", "-h", required=True, help="主机别名")
@click.option("--long", "-l", is_flag=True, help="详细列表")
@click.pass_context
def list_files(ctx, remote_path: str, host: str, long: bool):
    """列出远程目录内容

    REMOTE_PATH: 远程目录路径（默认当前目录）
    """
    try:
        ctx.obj["_conn"] = _conn_ctx(ctx, host)
        with _get_engine(host) as ssh:
            fm = FileManager(ssh)
            files = fm.list_files(remote_path)

            if _is_json(ctx):
                _output({"status": "ok", "path": remote_path, "files": files}, ctx)
                return

            if not files:
                click.echo("（空目录）")
                return

            if long:
                click.echo(f"{'类型':<6} {'大小':<12} {'修改时间':<18} {'名称'}")
                click.echo("-" * 60)
                for f in files:
                    ftype = "dir" if f["is_dir"] else "file"
                    size = _format_size(f["size"]) if not f["is_dir"] else "-"
                    click.echo(f"{ftype:<6} {size:<12} {f['modified']:<18} {f['name']}")
            else:
                for f in files:
                    suffix = "/" if f["is_dir"] else ""
                    click.echo(f"{f['name']}{suffix}")

    except (AgentError, FileNotFoundError) as e:
        _handle_error(e, ctx) if isinstance(e, AgentError) else _error(str(e), ctx)


# ---------------------------------------------------------------------------
# rm 命令
# ---------------------------------------------------------------------------

@cli.command("rm")
@click.argument("remote_path")
@click.option("--host", "-h", required=True, help="主机别名")
@click.option("--yes", "-y", is_flag=True, help="跳过确认")
@click.pass_context
def remove_file(ctx, remote_path: str, host: str, yes: bool):
    """删除远程文件或目录

    REMOTE_PATH: 远程文件或目录路径
    """
    try:
        ctx.obj["_conn"] = _conn_ctx(ctx, host)
        with _get_engine(host) as ssh:
            fm = FileManager(ssh)

            # 先检查是什么类型
            files = fm.list_files(str(Path(remote_path).parent))
            target_name = Path(remote_path).name
            is_dir = False
            for f in files:
                if f["name"] == target_name:
                    is_dir = f["is_dir"]
                    break

            # JSON 模式或 --yes 时跳过确认
            if is_dir and not yes and not _is_json(ctx):
                if not click.confirm(f"确定要删除目录 {remote_path} 及其所有内容吗？"):
                    return

            result = fm.delete(remote_path)
            _output({"status": "ok", "path": remote_path, "type": result["type"],
                     "message": f"已删除: {remote_path} ({result['type']})"}, ctx)

    except (AgentError, FileNotFoundError) as e:
        _handle_error(e, ctx) if isinstance(e, AgentError) else _error(str(e), ctx)


# ---------------------------------------------------------------------------
# proxy-dl 命令
# ---------------------------------------------------------------------------

@cli.command("proxy-dl")
@click.argument("url")
@click.argument("remote_path")
@click.option("--host", "-h", required=True, help="主机别名")
@click.option("--timeout", "-t", default=300, help="超时时间（秒）")
@click.pass_context
def proxy_download(ctx, url: str, remote_path: str, host: str, timeout: int):
    """在远程服务器上直接下载 URL 内容

    URL: 要下载的 URL
    REMOTE_PATH: 远程保存路径

    适用于：远程服务器可以直接下载文件，比本地下载再上传更快
    """
    try:
        ctx.obj["_conn"] = _conn_ctx(ctx, host)
        with _get_engine(host) as ssh:
            fm = FileManager(ssh)
            if not _is_json(ctx):
                click.echo(f"正在下载: {url}")
            result = fm.proxy_download(url, remote_path, timeout=timeout)
            _output({"status": "ok", "url": url, "remote": remote_path, "size": result["size"],
                     "message": f"已下载到: {remote_path} ({_format_size(result['size'])})"}, ctx)

    except AgentError as e:
        _handle_error(e, ctx)
