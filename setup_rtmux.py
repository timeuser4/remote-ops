#!/usr/bin/env python3
"""
rtmux 环境初始化脚本（跨平台：Windows / macOS / Linux）

功能：
1. 检查 Python 能否创建 venv
2. 在工作目录下创建 venv 并激活
3. 确保 paramiko 已安装
4. 安装 rtmux CLI
5. 安装 skill 文件到已检测的 harness
"""

import os
import sys
import shutil
import subprocess
import platform
import tempfile
from pathlib import Path


REPO_URL = "https://github.com/timeuser4/rtmux.git"
SCRIPT_DIR = Path(__file__).resolve().parent
VENV_DIR = SCRIPT_DIR / ".venv"
HARNESS_NAMES = {
    ".claude": "claude",
    ".codex": "codex",
    ".opencode": "opencode",
}


def log(msg: str) -> None:
    print(f"==> {msg}")


def warn(msg: str) -> None:
    print(f"  [!] {msg}")


def err(msg: str) -> None:
    print(f"  [ERROR] {msg}", file=sys.stderr)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """执行命令，捕获输出"""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


# ---------------------------------------------------------------------------
# Step 1: 检查 Python 能否创建 venv
# ---------------------------------------------------------------------------

def can_create_venv() -> bool:
    """检查 venv 模块是否可用"""
    try:
        import venv  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Step 2: 创建或定位 venv
# ---------------------------------------------------------------------------

def get_venv_python() -> Path:
    """获取 venv 中的 Python 路径"""
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def get_venv_pip() -> Path:
    """获取 venv 中的 pip 路径"""
    if platform.system() == "Windows":
        return VENV_DIR / "Scripts" / "pip.exe"
    return VENV_DIR / "bin" / "pip"


def get_venv_activate_hint() -> str:
    """获取 venv 激活命令（供用户参考）"""
    if platform.system() == "Windows":
        return str(VENV_DIR / "Scripts" / "activate.bat")
    return f"source {VENV_DIR}/bin/activate"


def venv_is_valid() -> bool:
    """检查 venv 是否可用（Python 和 pip 都能正常工作）"""
    venv_python = get_venv_python()
    venv_pip = get_venv_pip()
    if not venv_python.exists():
        return False
    # 检查 Python 能运行
    result = run([str(venv_python), "-c", "import sys; print(sys.version)"])
    if result.returncode != 0:
        return False
    # 检查 pip 能运行
    result = run([str(venv_python), "-m", "pip", "--version"])
    if result.returncode != 0:
        return False
    return True


def ensure_venv() -> Path:
    """
    确保 venv 存在且可用，返回 venv Python 路径

    如果 venv 已存在且正常则跳过创建，否则重建
    """
    if VENV_DIR.exists() and not venv_is_valid():
        warn(f"venv 损坏，重新创建: {VENV_DIR}")
        shutil.rmtree(VENV_DIR)

    if VENV_DIR.exists() and venv_is_valid():
        log(f"venv 已存在: {VENV_DIR}")
        return get_venv_python()

    log(f"创建 venv: {VENV_DIR}")
    result = run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if result.returncode != 0:
        err(f"创建 venv 失败: {result.stderr.strip()}")
        sys.exit(1)

    # 升级 pip
    log("升级 pip...")
    run([str(get_venv_python()), "-m", "pip", "install", "--upgrade", "pip"])

    log("venv 创建完成")
    return get_venv_python()


# ---------------------------------------------------------------------------
# Step 3: 确保 paramiko 已安装
# ---------------------------------------------------------------------------

def ensure_paramiko(venv_python: Path) -> None:
    """检查 paramiko 是否存在，不存在则安装"""
    result = run([str(venv_python), "-c", "import paramiko; print(paramiko.__version__)"])
    if result.returncode == 0:
        log(f"paramiko 已安装: {result.stdout.strip()}")
        return

    log("安装 paramiko...")
    pip = get_venv_pip()
    result = run([str(pip), "install", "paramiko"])
    if result.returncode != 0:
        err(f"安装 paramiko 失败: {result.stderr.strip()}")
        sys.exit(1)

    # 验证
    result = run([str(venv_python), "-c", "import paramiko; print(paramiko.__version__)"])
    if result.returncode == 0:
        log(f"paramiko 安装成功: {result.stdout.strip()}")
    else:
        err("paramiko 安装后验证失败")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Step 4: 安装 rtmux CLI
# ---------------------------------------------------------------------------

def install_rtmux(venv_python: Path) -> None:
    """安装 rtmux 包到 venv"""
    pip = get_venv_pip()

    # 检查是否已安装
    result = run([str(venv_python), "-m", "rtmux", "--help"])
    if result.returncode == 0:
        log("rtmux 已安装")
        return

    # 从本地目录安装
    log("安装 rtmux...")
    result = run([str(pip), "install", "-e", str(SCRIPT_DIR)])
    if result.returncode != 0:
        err(f"安装 rtmux 失败: {result.stderr.strip()}")
        sys.exit(1)

    log("rtmux 安装完成")


# ---------------------------------------------------------------------------
# Step 5: 安装 skill 文件
# ---------------------------------------------------------------------------

def detect_harnesses() -> list[tuple[str, Path]]:
    """检测已安装的 harness，返回 [(name, skill_dir), ...]"""
    home = Path.home()
    found = []
    for dirname, name in HARNESS_NAMES.items():
        harness_dir = home / dirname
        if harness_dir.exists():
            skill_dir = harness_dir / "skills" / "rtmux"
            found.append((name, skill_dir))
    return found


def install_skill_files(harnesses: list[tuple[str, Path]]) -> None:
    """安装 SKILL.md、references、agents 到各 harness"""
    if not harnesses:
        warn("未检测到 harness（~/.claude, ~/.codex, ~/.opencode），跳过 skill 安装")
        return

    skill_src = SCRIPT_DIR / "SKILL.md"
    refs_src = SCRIPT_DIR / "references"
    agents_src = SCRIPT_DIR / "agents"

    if not skill_src.exists():
        warn("SKILL.md 不存在，跳过 skill 安装")
        return

    for name, skill_dir in harnesses:
        log(f"安装 skill 到 {name}: {skill_dir}")
        skill_dir.mkdir(parents=True, exist_ok=True)

        shutil.copy2(skill_src, skill_dir / "SKILL.md")

        if refs_src.exists():
            dst_refs = skill_dir / "references"
            if dst_refs.exists():
                shutil.rmtree(dst_refs)
            shutil.copytree(refs_src, dst_refs)

        if agents_src.exists():
            dst_agents = skill_dir / "agents"
            if dst_agents.exists():
                shutil.rmtree(dst_agents)
            shutil.copytree(agents_src, dst_agents)


# ---------------------------------------------------------------------------
# Step 6: 远程服务器一次性初始化
# ---------------------------------------------------------------------------

def check_server_in_config(alias: str) -> bool:
    """检查服务器是否已在 ~/.agent/connections.json 中"""
    config_file = Path.home() / ".agent" / "connections.json"
    if not config_file.exists():
        return False
    try:
        import json
        data = json.loads(config_file.read_text())
        return alias in data.get("connections", {})
    except Exception:
        return False


def init_server(venv_python: Path, target: str, port: int = 22, password: str = "") -> None:
    """
    远程服务器一次性初始化

    1. 检查是否已在 config 中
    2. 生成 SSH 密钥
    3. 上传公钥
    4. 安装远程 tmux
    5. 保存连接配置
    """
    # 解析 target
    if "@" not in target:
        err(f"目标格式错误: {target}，应为 user@host")
        sys.exit(1)
    username, hostname = target.split("@", 1)

    # 生成别名
    import socket
    local_host = socket.gethostname().replace(".", "-")
    try:
        ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        ip = hostname
    safe_ip = ip.replace(".", "-")
    alias = f"{local_host}_{username}_{safe_ip}"

    # 检查是否已存在
    if check_server_in_config(alias):
        log(f"服务器已在配置中，跳过: {alias}")
        return

    log(f"初始化服务器: {username}@{hostname}:{port}")

    # 通过 rtmux connect 完成初始化
    env = os.environ.copy()
    if password:
        env["RTMUX_PASSWORD"] = password
        password_arg = "--password-env RTMUX_PASSWORD"
    else:
        password_arg = ""

    cmd = [
        str(venv_python), "-m", "rtmux", "connect",
        target,
        "--port", str(port),
    ]
    if password_arg:
        cmd.extend(["--password-env", "RTMUX_PASSWORD"])

    result = run(cmd, env=env)
    if result.returncode != 0:
        err(f"服务器初始化失败: {result.stderr.strip()}")
        sys.exit(1)

    log(f"服务器初始化完成: {alias}")
    print(result.stdout)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def main():
    print("=" * 50)
    print("  rtmux 环境初始化")
    print("=" * 50)
    print(f"  平台: {platform.system()} {platform.machine()}")
    print(f"  Python: {sys.executable} ({sys.version.split()[0]})")
    print(f"  工作目录: {SCRIPT_DIR}")
    print()

    # Step 1: 检查 venv 能力
    if not can_create_venv():
        warn("Python 缺少 venv 模块，跳过 venv 创建")
        warn("请手动安装 paramiko: pip install paramiko")
        venv_python = Path(sys.executable)
    else:
        # Step 2: 创建/定位 venv
        venv_python = ensure_venv()

    # Step 3: 确保 paramiko
    ensure_paramiko(venv_python)

    # Step 4: 安装 rtmux
    install_rtmux(venv_python)

    # Step 5: 安装 skill
    harnesses = detect_harnesses()
    install_skill_files(harnesses)

    print()
    print("=" * 50)
    print("  初始化完成！")
    print("=" * 50)
    print()
    print(f"  激活 venv: {get_venv_activate_hint()}")
    print(f"  使用 rtmux: rtmux connections")
    print()

    # Step 6: 可选 - 初始化远程服务器
    if len(sys.argv) > 1:
        target = sys.argv[1]
        port = 22
        password = ""
        # 解析额外参数
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
                i += 2
            elif sys.argv[i] == "--password" and i + 1 < len(sys.argv):
                password = sys.argv[i + 1]
                i += 2
            else:
                i += 1
        init_server(venv_python, target, port, password)
    else:
        print("  连接远程服务器:")
        print("    rtmux connect user@host")
        print("    python setup.py user@host --port 6000 --password xxx")
        print()


if __name__ == "__main__":
    main()
