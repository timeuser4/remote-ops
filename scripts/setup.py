#!/usr/bin/env python3
"""
Cross-platform setup hook for remote-ops skill.

Detects OS and architecture, then installs the appropriate remote tool:
  - Windows: downloads and installs the correct PuTTY plink .msi
  - macOS: brew install sshpass
  - Linux: apt/dnf/pacman/zypper install sshpass
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

PLINK_BASE = "https://the.earth.li/~sgtatham/putty/latest"

PLINK_URLS = {
    "windows-amd64": f"{PLINK_BASE}/w64/putty-64bit-0.83-installer.msi",
    "windows-arm64": f"{PLINK_BASE}/wa64/putty-arm64-0.83-installer.msi",
    "windows-x86": f"{PLINK_BASE}/w32/putty-0.83-installer.msi",
}


def detect_os() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "macos"
    elif system == "linux":
        return "linux"
    else:
        raise RuntimeError(f"Unsupported OS: {system}")


def detect_windows_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "windows-amd64"
    elif machine in ("arm64", "aarch64"):
        return "windows-arm64"
    elif machine == "i386":
        return "windows-x86"
    else:
        env_arch = os.environ.get("PROCESSOR_ARCHITECTURE", "").lower()
        if env_arch == "amd64":
            return "windows-amd64"
        elif env_arch == "arm64":
            return "windows-arm64"
        elif env_arch == "x86":
            return "windows-x86"
        raise RuntimeError(f"Cannot detect Windows architecture: {machine}")


def find_plink() -> str | None:
    import shutil

    found = shutil.which("plink") or shutil.which("plink.exe")
    if found:
        return found

    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "PuTTY" / "plink.exe",
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "PuTTY" / "plink.exe",
        Path(os.environ.get("LocalAppData", r"")) / "Programs" / "PuTTY" / "plink.exe",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def install_plink_windows() -> bool:
    existing = find_plink()
    if existing:
        print(f"plink.exe already installed: {existing}")
        return True

    arch = detect_windows_arch()
    url = PLINK_URLS[arch]
    print(f"Detected architecture: {arch}")
    print(f"Downloading: {url}")

    dest = Path(tempfile.gettempdir()) / "putty-installer.msi"
    urllib.request.urlretrieve(url, str(dest))
    print(f"Downloaded to: {dest}")
    print("Running installer...")
    subprocess.run(["msiexec", "/i", str(dest), "/passive"], check=True)
    dest.unlink(missing_ok=True)

    new_plink = find_plink()
    if new_plink:
        print(f"plink installed: {new_plink}")
        return True
    else:
        print("Installation may have succeeded but plink.exe not found in expected paths.")
        print("Check C:\\Program Files\\PuTTY\\plink.exe")
        return False


def install_sshpass_macos() -> bool:
    if subprocess.run(["which", "sshpass"], capture_output=True).returncode == 0:
        print("sshpass already installed.")
        return True
    if subprocess.run(["which", "brew"], capture_output=True).returncode != 0:
        print("Homebrew not found. Install it first: https://brew.sh")
        return False
    print("Installing sshpass via Homebrew...")
    subprocess.run(["brew", "install", "hudochenkov/sshpass/sshpass"], check=True)
    return True


def detect_linux_pkg_manager() -> str | None:
    for manager in ["apt", "dnf", "yum", "pacman", "zypper"]:
        if subprocess.run(["which", manager], capture_output=True).returncode == 0:
            return manager
    return None


def install_sshpass_linux() -> bool:
    if subprocess.run(["which", "sshpass"], capture_output=True).returncode == 0:
        print("sshpass already installed.")
        return True

    pkg = detect_linux_pkg_manager()
    if not pkg:
        print("No supported package manager found (apt/dnf/yum/pacman/zypper).")
        print("Install sshpass manually, then re-run.")
        return False

    print(f"Found package manager: {pkg}")

    if pkg == "pacman":
        subprocess.run(["sudo", "pacman", "-S", "--noconfirm", "sshpass"], check=True)
    elif pkg == "zypper":
        subprocess.run(["sudo", "zypper", "install", "-y", "sshpass"], check=True)
    else:
        subprocess.run(["sudo", pkg, "install", "-y", "sshpass"], check=True)

    return True


def main() -> int:
    os_type = detect_os()
    print(f"Detected OS: {os_type}")

    if os_type == "windows":
        success = install_plink_windows()
    elif os_type == "macos":
        success = install_sshpass_macos()
    elif os_type == "linux":
        success = install_sshpass_linux()
    else:
        print(f"Unsupported OS: {os_type}")
        return 1

    if success:
        print("Setup complete.")
        return 0
    else:
        print("Setup failed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
