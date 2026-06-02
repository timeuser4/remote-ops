"""远程 tmux 检测与安装"""

import click

from .ssh_engine import SSHEngine
from .exceptions import TmuxError


REMOTE_TMUX_PATH = "~/.local/bin/tmux"

# 包管理器安装命令（优先级从高到低）
PKG_MANAGERS = [
    # (检测命令, 安装命令)
    ("apt-get --version", "sudo -n apt-get update -qq && sudo -n apt-get install -y -qq tmux"),
    ("dnf --version", "sudo -n dnf install -y -q tmux"),
    ("yum --version", "sudo -n yum install -y -q tmux"),
    ("pacman --version", "sudo -n pacman -S --noconfirm tmux"),
    ("zypper --version", "sudo -n zypper install -y tmux"),
    ("apk --version", "sudo -n apk add tmux"),
]


class TmuxInstaller:
    """检测并安装远程主机上的 tmux"""

    def __init__(self, ssh: SSHEngine) -> None:
        self.ssh = ssh

    def check_installed(self) -> bool:
        """
        检查 tmux 是否已安装且可用

        验证：命令存在、输出版本号、文件非空
        """
        # 检查用户级安装
        result = self.ssh.exec(f"{REMOTE_TMUX_PATH} -V 2>/dev/null")
        if result.ok and result.stdout.strip():
            return True

        # 检查系统级安装
        result = self.ssh.exec("command -v tmux >/dev/null 2>&1 && tmux -V 2>/dev/null")
        if result.ok and result.stdout.strip():
            return True

        # 清理无效的空文件
        self.ssh.exec(f"find ~/.local/bin -name tmux -empty -delete 2>/dev/null")

        return False

    def detect_arch(self) -> str:
        """检测远程主机架构"""
        result = self.ssh.exec("uname -m")
        if result.failed:
            raise TmuxError("无法检测远程架构")
        return result.stdout.strip()

    def verify_path_in_env(self) -> None:
        """确保 ~/.local/bin 在远程 PATH 中"""
        # 检查是否已在 PATH 中
        result = self.ssh.exec('echo "$PATH" | grep -q "$HOME/.local/bin"')
        if result.ok:
            return

        # 添加到 shell 配置
        self.ssh.exec("mkdir -p ~/.local/bin")

        # 检测 shell 类型
        shell_result = self.ssh.exec("basename $SHELL")
        shell = shell_result.stdout.strip()

        rc_file = "~/.zshrc" if shell == "zsh" else "~/.bashrc"

        # 添加 PATH 导出
        export_line = 'export PATH="$HOME/.local/bin:$PATH"'
        self.ssh.exec(
            f'grep -qF \'.local/bin\' {rc_file} 2>/dev/null || '
            f'echo \'{export_line}\' >> {rc_file}'
        )

        # 同时添加到 .profile（登录 shell）
        self.ssh.exec(
            f'grep -qF \'.local/bin\' ~/.profile 2>/dev/null || '
            f'echo \'{export_line}\' >> ~/.profile'
        )

    def try_install_via_pkg_manager(self) -> bool:
        """
        尝试通过系统包管理器安装 tmux

        Returns:
            True 如果安装成功
        """
        for check_cmd, install_cmd in PKG_MANAGERS:
            result = self.ssh.exec(f"{check_cmd} 2>/dev/null")
            if result.ok:
                click.echo(f"  尝试通过包管理器安装...")
                result = self.ssh.exec(install_cmd, timeout=180)
                if result.ok and self.check_installed():
                    click.echo("  tmux 已通过包管理器安装")
                    return True
                break
        return False

    def try_install_from_source(self) -> bool:
        """
        尝试从源码编译安装 tmux

        需要 gcc/make 等编译工具，安装到 ~/.local/

        Returns:
            True 如果安装成功
        """
        # 检查编译工具
        result = self.ssh.exec("command -v gcc && command -v make 2>/dev/null")
        if result.failed:
            return False

        click.echo("  从源码编译 tmux...")

        # 检查依赖库
        self.ssh.exec("mkdir -p ~/.local/src")

        # 下载源码
        tmux_ver = "3.4"
        url = f"https://github.com/tmux/tmux/releases/download/{tmux_ver}/tmux-{tmux_ver}.tar.gz"
        result = self.ssh.exec(
            f"cd ~/.local/src && "
            f"wget -q --timeout=60 -O tmux-{tmux_ver}.tar.gz '{url}' 2>&1 || "
            f"curl -fsSL -o tmux-{tmux_ver}.tar.gz '{url}' 2>&1",
            timeout=120,
        )
        if result.failed:
            click.echo("  源码下载失败")
            return False

        # 解压、编译、安装
        cmds = [
            f"cd ~/.local/src && tar xzf tmux-{tmux_ver}.tar.gz",
            f"cd ~/.local/src/tmux-{tmux_ver} && ./configure --prefix=$HOME/.local --enable-static 2>&1 | tail -3",
            f"cd ~/.local/src/tmux-{tmux_ver} && make -j$(nproc) 2>&1 | tail -3",
            f"cd ~/.local/src/tmux-{tmux_ver} && make install 2>&1 | tail -3",
        ]
        for cmd in cmds:
            result = self.ssh.exec(cmd, timeout=300)
            if result.failed:
                click.echo(f"  编译失败: {result.stderr.strip()[:100]}")
                return False

        # 清理源码
        self.ssh.exec("rm -rf ~/.local/src/tmux-*")

        return self.check_installed()

    def download_tmux_binary(self, arch: str) -> bool:
        """
        下载预编译的 tmux 静态二进制

        尝试多个下载源

        Returns:
            True 如果下载成功
        """
        self.ssh.exec("mkdir -p ~/.local/bin")

        # 尝试多个来源
        sources = [
            # tmux-Appimage（可能 404）
            f"https://github.com/nelsonenzo/tmux-appimage/releases/download/3.3a/tmux-3.3a-{arch}.AppImage",
            # 备用：直接从 tmux releases 下载
            f"https://github.com/tmux/tmux/releases/download/3.4/tmux-3.4-{arch}.linux.tar.gz",
        ]

        for url in sources:
            click.echo(f"  尝试下载: {url.split('/')[-1]}...")
            if url.endswith(".tar.gz"):
                # tar.gz 格式，需要解压
                result = self.ssh.exec(
                    f"cd ~/.local/bin && "
                    f"wget -q --timeout=60 -O tmux-bin.tar.gz '{url}' 2>&1 || "
                    f"curl -fsSL -o tmux-bin.tar.gz '{url}' 2>&1",
                    timeout=120,
                )
                if result.ok:
                    self.ssh.exec("cd ~/.local/bin && tar xzf tmux-bin.tar.gz --strip-components=1")
                    self.ssh.exec("rm -f ~/.local/bin/tmux-bin.tar.gz")
                    if self.check_installed():
                        return True
            else:
                # 直接二进制/AppImage
                result = self.ssh.exec(
                    f"wget -q --timeout=60 -O {REMOTE_TMUX_PATH} '{url}' 2>&1 || "
                    f"curl -fsSL -o {REMOTE_TMUX_PATH} '{url}' 2>&1",
                    timeout=120,
                )
                if result.ok:
                    self.ssh.exec(f"chmod +x {REMOTE_TMUX_PATH}")
                    # 检查文件大小（> 100KB 才算成功）
                    r = self.ssh.exec(f"stat -c%s {REMOTE_TMUX_PATH} 2>/dev/null || wc -c < {REMOTE_TMUX_PATH}")
                    try:
                        size = int(r.stdout.strip())
                        if size > 100000 and self.check_installed():
                            return True
                    except ValueError:
                        pass
                    # 文件无效，清理
                    self.ssh.exec(f"rm -f {REMOTE_TMUX_PATH}")

        return False

    def verify_installation(self) -> bool:
        """验证 tmux 安装是否成功"""
        result = self.ssh.exec(f"{REMOTE_TMUX_PATH} -V 2>/dev/null || tmux -V 2>/dev/null")
        if result.ok:
            version = result.stdout.strip()
            click.echo(f"  tmux 已安装: {version}")
            return True
        return False

    def ensure_tmux(self) -> None:
        """
        编排 tmux 检查与安装流程

        优先级：
        1. 已安装 → 跳过
        2. 系统包管理器安装
        3. 下载预编译二进制
        4. 从源码编译
        """
        if self.check_installed():
            click.echo("  tmux 已存在，跳过安装")
            return

        click.echo("  tmux 未安装，开始安装...")

        # 确保 PATH 配置
        self.verify_path_in_env()

        # 方式1：包管理器
        if self.try_install_via_pkg_manager():
            return

        # 方式2：预编译二进制
        arch = self.detect_arch()
        if self.download_tmux_binary(arch):
            if not self.verify_installation():
                raise TmuxError("tmux 安装后验证失败")
            click.echo("  tmux 安装完成（预编译二进制）")
            return

        # 方式3：源码编译
        if self.try_install_from_source():
            if not self.verify_installation():
                raise TmuxError("tmux 安装后验证失败")
            click.echo("  tmux 安装完成（源码编译）")
            return

        raise TmuxError(
            "tmux 安装失败：所有方式均未成功\n"
            "请手动安装：sudo apt install tmux 或从 https://github.com/tmux/tmux 编译"
        )
