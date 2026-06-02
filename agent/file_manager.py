"""文件管理：SFTP 上传/下载/列表/删除/代理下载"""

import os
import stat
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

import click

from .ssh_engine import SSHEngine
from .exceptions import ConnectionError


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


class FileManager:
    """基于 SFTP 的远程文件管理"""

    def __init__(self, ssh: SSHEngine) -> None:
        self.ssh = ssh
        self._sftp = None

    def _get_sftp(self):
        """获取 SFTP 客户端（懒加载）"""
        if self._sftp is None:
            if not self.ssh._client:
                raise ConnectionError("SSH 未连接")
            self._sftp = self.ssh._client.open_sftp()
        return self._sftp

    def close(self) -> None:
        """关闭 SFTP 连接"""
        if self._sftp:
            self._sftp.close()
            self._sftp = None

    # -----------------------------------------------------------------------
    # 通用复制（cp）
    # -----------------------------------------------------------------------

    def cp(
        self,
        src: str,
        dst: str,
        recursive: bool = False,
        resume: bool = False,
        show_progress: bool = True,
    ) -> Dict:
        """
        通用文件复制（类似 scp）

        通过 :: 前缀判断方向（双冒号避免 Windows 盘符 C: 冲突）：
        - src 带 :: → 远程 → 本地（下载）
        - src 不带 :: → 本地 → 远程（上传）

        Args:
            src: 源路径（带 :: 前缀表示远程）
            dst: 目标路径（带 :: 前缀表示远程）
            recursive: 递归复制目录
            resume: 断点续传
            show_progress: 显示进度

        Returns:
            复制结果信息
        """
        src_is_remote = src.startswith("::")
        dst_is_remote = dst.startswith("::")
        src_path = src[2:] if src_is_remote else src
        dst_path = dst[2:] if dst_is_remote else dst

        if src_is_remote == dst_is_remote:
            raise ValueError("源和目标必须一个是本地路径，一个是远程路径（用 :: 前缀标识远程）")

        if src_is_remote:
            return self._download(src_path, dst_path, recursive=recursive, resume=resume, show_progress=show_progress)
        else:
            return self._upload(src_path, dst_path, recursive=recursive, resume=resume, show_progress=show_progress)

    def _upload(
        self,
        local_path: str,
        remote_path: str,
        recursive: bool = False,
        resume: bool = False,
        show_progress: bool = True,
    ) -> Dict:
        """上传：本地 → 远程"""
        local = Path(local_path).expanduser().resolve()
        if not local.exists():
            raise FileNotFoundError(f"本地路径不存在: {local_path}")

        sftp = self._get_sftp()

        if local.is_dir():
            if not recursive:
                raise ValueError(f"本地路径是目录，需要使用 -r 递归上传: {local_path}")
            return self._upload_dir(sftp, local, remote_path, show_progress)

        file_size = local.stat().st_size

        # 如果远程路径是目录，自动拼接文件名
        try:
            remote_stat = sftp.stat(remote_path)
            if stat.S_ISDIR(remote_stat.st_mode):
                remote_path = f"{remote_path.rstrip('/')}/{local.name}"
        except FileNotFoundError:
            pass

        # 确保远程目录存在
        self._mkdir_p(sftp, str(Path(remote_path).parent))

        # 断点续传：检查远程已有大小
        remote_offset = 0
        if resume:
            try:
                remote_stat = sftp.stat(remote_path)
                remote_offset = remote_stat.st_size
                if remote_offset >= file_size:
                    return {"status": "ok", "local": str(local), "remote": remote_path,
                            "size": file_size, "message": "文件已是最新，无需传输"}
            except FileNotFoundError:
                pass

        if resume and remote_offset > 0:
            # 续传：append 模式
            if show_progress:
                remaining = file_size - remote_offset
                click.echo(f"  续传: 从 {_format_size(remote_offset)} 开始，剩余 {_format_size(remaining)}")
            with open(str(local), 'rb') as f:
                f.seek(remote_offset)
                with sftp.open(remote_path, 'a') as rf:
                    while True:
                        chunk = f.read(65536)
                        if not chunk:
                            break
                        rf.write(chunk)
        else:
            # 完整上传
            if show_progress:
                with click.progressbar(length=file_size, label=f"上传 {local.name}") as bar:
                    transferred = [0]
                    def cb(sent, total):
                        delta = sent - transferred[0]
                        bar.update(delta)
                        transferred[0] = sent
                    sftp.put(str(local), remote_path, callback=cb)
            else:
                sftp.put(str(local), remote_path)

        return {"status": "ok", "local": str(local), "remote": remote_path, "size": file_size}

    def _upload_dir(self, sftp, local_dir: Path, remote_dir: str, show_progress: bool) -> Dict:
        """递归上传目录"""
        files = [f for f in local_dir.rglob("*") if f.is_file()]
        total_size = sum(f.stat().st_size for f in files)

        for f in files:
            rel = f.relative_to(local_dir)
            remote_file = f"{remote_dir}/{rel}"
            self._mkdir_p(sftp, str(Path(remote_file).parent))
            if show_progress:
                click.echo(f"  {rel}")
            sftp.put(str(f), remote_file)

        return {"status": "ok", "local": str(local_dir), "remote": remote_dir,
                "files": len(files), "size": total_size}

    def _download(
        self,
        remote_path: str,
        local_path: str,
        recursive: bool = False,
        resume: bool = False,
        show_progress: bool = True,
    ) -> Dict:
        """下载：远程 → 本地"""
        sftp = self._get_sftp()

        try:
            remote_stat = sftp.stat(remote_path)
        except FileNotFoundError:
            raise FileNotFoundError(f"远程路径不存在: {remote_path}")

        is_dir = stat.S_ISDIR(remote_stat.st_mode) if remote_stat.st_mode else False

        if is_dir:
            if not recursive:
                raise ValueError(f"远程路径是目录，需要使用 -r 递归下载: {remote_path}")
            return self._download_dir(sftp, remote_path, local_path, show_progress)

        file_size = remote_stat.st_size
        local = Path(local_path).expanduser().resolve()

        # 如果目标是目录，文件放在目录下
        if local.is_dir():
            local = local / Path(remote_path).name

        local.parent.mkdir(parents=True, exist_ok=True)

        # 断点续传：检查本地已有大小
        local_offset = 0
        if resume and local.exists():
            local_offset = local.stat().st_size
            if local_offset >= file_size:
                return {"status": "ok", "remote": remote_path, "local": str(local),
                        "size": file_size, "message": "文件已是最新，无需传输"}

        if resume and local_offset > 0:
            # 续传：从断点下载
            remaining = file_size - local_offset
            if show_progress:
                click.echo(f"  续传: 从 {_format_size(local_offset)} 开始，剩余 {_format_size(remaining)}")
            with sftp.open(remote_path, 'r') as rf:
                rf.seek(local_offset)
                with open(str(local), 'ab') as f:
                    while True:
                        chunk = rf.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
        else:
            # 完整下载
            if show_progress:
                with click.progressbar(length=file_size, label=f"下载 {Path(remote_path).name}") as bar:
                    transferred = [0]
                    def cb(received, total):
                        delta = received - transferred[0]
                        bar.update(delta)
                        transferred[0] = received
                    sftp.get(remote_path, str(local), callback=cb)
            else:
                sftp.get(remote_path, str(local))

        return {"status": "ok", "remote": remote_path, "local": str(local), "size": file_size}

    def _download_dir(self, sftp, remote_dir: str, local_dir: str, show_progress: bool) -> Dict:
        """递归下载目录"""
        local = Path(local_dir).expanduser().resolve()
        local.mkdir(parents=True, exist_ok=True)

        files = self._list_remote_recursive(sftp, remote_dir)
        total_size = sum(f["size"] for f in files)

        for f in files:
            rel = f["path"][len(remote_dir):].lstrip("/")
            local_file = local / rel
            local_file.parent.mkdir(parents=True, exist_ok=True)
            if show_progress:
                click.echo(f"  {rel}")
            sftp.get(f["path"], str(local_file))

        return {"status": "ok", "remote": remote_dir, "local": str(local),
                "files": len(files), "size": total_size}

    # -----------------------------------------------------------------------
    # 上传（旧接口，保留兼容）
    # -----------------------------------------------------------------------

    def upload(
        self,
        local_path: str,
        remote_path: str,
        show_progress: bool = True,
    ) -> Dict:
        """
        上传本地文件到远程

        Args:
            local_path: 本地文件路径
            remote_path: 远程目标路径
            show_progress: 是否显示进度

        Returns:
            上传结果信息
        """
        local = Path(local_path).expanduser().resolve()
        if not local.exists():
            raise FileNotFoundError(f"本地文件不存在: {local_path}")

        sftp = self._get_sftp()
        file_size = local.stat().st_size

        # 确保远程目录存在
        remote_dir = str(Path(remote_path).parent)
        self._mkdir_p(sftp, remote_dir)

        if show_progress:
            with click.progressbar(
                length=file_size,
                label=f"上传 {local.name}",
            ) as bar:
                transferred = [0]

                def callback(sent, total):
                    delta = sent - transferred[0]
                    bar.update(delta)
                    transferred[0] = sent

                sftp.put(str(local), remote_path, callback=callback)
        else:
            sftp.put(str(local), remote_path)

        return {
            "status": "success",
            "local_path": str(local),
            "remote_path": remote_path,
            "size": file_size,
        }

    def upload_dir(
        self,
        local_dir: str,
        remote_dir: str,
        show_progress: bool = True,
    ) -> Dict:
        """
        递归上传整个目录

        Returns:
            上传结果信息
        """
        local = Path(local_dir).expanduser().resolve()
        if not local.is_dir():
            raise FileNotFoundError(f"本地目录不存在: {local_dir}")

        sftp = self._get_sftp()
        files = list(local.rglob("*"))
        files = [f for f in files if f.is_file()]

        total_size = sum(f.stat().st_size for f in files)
        uploaded = 0

        for f in files:
            rel = f.relative_to(local)
            remote_file = f"{remote_dir}/{rel}"

            # 确保远程目录存在
            self._mkdir_p(sftp, str(Path(remote_file).parent))

            if show_progress:
                click.echo(f"  {rel}")

            sftp.put(str(f), remote_file)
            uploaded += f.stat().st_size

        return {
            "status": "success",
            "local_dir": str(local),
            "remote_dir": remote_dir,
            "files": len(files),
            "size": total_size,
        }

    # -----------------------------------------------------------------------
    # 下载
    # -----------------------------------------------------------------------

    def download(
        self,
        remote_path: str,
        local_path: str,
        show_progress: bool = True,
    ) -> Dict:
        """
        从远程下载文件到本地

        Args:
            remote_path: 远程文件路径
            local_path: 本地目标路径
            show_progress: 是否显示进度

        Returns:
            下载结果信息
        """
        sftp = self._get_sftp()

        # 获取远程文件大小
        try:
            remote_stat = sftp.stat(remote_path)
            file_size = remote_stat.st_size
        except FileNotFoundError:
            raise FileNotFoundError(f"远程文件不存在: {remote_path}")

        # 确保本地目录存在
        local = Path(local_path).expanduser().resolve()
        local.parent.mkdir(parents=True, exist_ok=True)

        if show_progress:
            with click.progressbar(
                length=file_size,
                label=f"下载 {Path(remote_path).name}",
            ) as bar:
                transferred = [0]

                def callback(received, total):
                    delta = received - transferred[0]
                    bar.update(delta)
                    transferred[0] = received

                sftp.get(remote_path, str(local), callback=callback)
        else:
            sftp.get(remote_path, str(local))

        return {
            "status": "success",
            "remote_path": remote_path,
            "local_path": str(local),
            "size": file_size,
        }

    def download_dir(
        self,
        remote_dir: str,
        local_dir: str,
        show_progress: bool = True,
    ) -> Dict:
        """
        递归下载整个目录

        Returns:
            下载结果信息
        """
        sftp = self._get_sftp()
        local = Path(local_dir).expanduser().resolve()
        local.mkdir(parents=True, exist_ok=True)

        files = self._list_remote_recursive(sftp, remote_dir)
        total_size = sum(f["size"] for f in files)
        downloaded = 0

        for f in files:
            rel = f["path"][len(remote_dir):].lstrip("/")
            local_file = local / rel

            local_file.parent.mkdir(parents=True, exist_ok=True)

            if show_progress:
                click.echo(f"  {rel}")

            sftp.get(f["path"], str(local_file))
            downloaded += f["size"]

        return {
            "status": "success",
            "remote_dir": remote_dir,
            "local_dir": str(local),
            "files": len(files),
            "size": total_size,
        }

    # -----------------------------------------------------------------------
    # 文件列表
    # -----------------------------------------------------------------------

    def list_files(self, remote_path: str = ".") -> List[Dict]:
        """
        列出远程目录内容

        Returns:
            文件信息列表
        """
        sftp = self._get_sftp()

        try:
            entries = sftp.listdir_attr(remote_path)
        except FileNotFoundError:
            raise FileNotFoundError(f"远程路径不存在: {remote_path}")

        result = []
        for entry in entries:
            is_dir = stat.S_ISDIR(entry.st_mode) if entry.st_mode else False
            result.append({
                "name": entry.filename,
                "size": entry.st_size or 0,
                "is_dir": is_dir,
                "modified": datetime.fromtimestamp(entry.st_mtime).strftime("%Y-%m-%d %H:%M")
                if entry.st_mtime else "",
            })

        # 排序：目录在前，然后按名称
        result.sort(key=lambda x: (not x["is_dir"], x["name"]))
        return result

    # -----------------------------------------------------------------------
    # 文件删除
    # -----------------------------------------------------------------------

    def delete(self, remote_path: str) -> Dict:
        """
        删除远程文件或目录

        Returns:
            删除结果信息
        """
        sftp = self._get_sftp()

        try:
            attr = sftp.stat(remote_path)
        except FileNotFoundError:
            raise FileNotFoundError(f"远程路径不存在: {remote_path}")

        is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False

        if is_dir:
            self._rmdir_recursive(sftp, remote_path)
            return {"status": "success", "path": remote_path, "type": "directory"}
        else:
            sftp.remove(remote_path)
            return {"status": "success", "path": remote_path, "type": "file"}

    # -----------------------------------------------------------------------
    # 代理下载
    # -----------------------------------------------------------------------

    def proxy_download(
        self,
        url: str,
        remote_path: str,
        timeout: int = 300,
    ) -> Dict:
        """
        在远程服务器上下载 URL 内容

        用途：远程服务器可以直接下载文件，比本地下载再上传更快

        Args:
            url: 要下载的 URL
            remote_path: 远程保存路径
            timeout: 超时时间（秒）

        Returns:
            下载结果信息
        """
        # 确保远程目录存在
        remote_dir = str(Path(remote_path).parent)
        self.ssh.exec(f"mkdir -p {remote_dir}")

        # 尝试 wget，失败则用 curl
        cmd = (
            f"wget -q -O '{remote_path}' '{url}' 2>&1 || "
            f"curl -fsSL -o '{remote_path}' '{url}' 2>&1"
        )

        result = self.ssh.exec(cmd, timeout=timeout)
        if result.failed:
            raise ConnectionError(f"代理下载失败: {result.stdout or result.stderr}")

        # 获取下载的文件大小
        size_result = self.ssh.exec(f"stat -c%s '{remote_path}' 2>/dev/null || stat -f%z '{remote_path}'")
        size = int(size_result.stdout.strip()) if size_result.ok else 0

        return {
            "status": "success",
            "url": url,
            "remote_path": remote_path,
            "size": size,
        }

    # -----------------------------------------------------------------------
    # 辅助方法
    # -----------------------------------------------------------------------

    def _mkdir_p(self, sftp, remote_dir: str) -> None:
        """递归创建远程目录"""
        if remote_dir in (".", "/", ""):
            return

        dirs_to_create = []
        current = remote_dir

        while current and current != "/":
            try:
                sftp.stat(current)
                break  # 目录已存在
            except FileNotFoundError:
                dirs_to_create.insert(0, current)
                current = str(Path(current).parent)

        for d in dirs_to_create:
            try:
                sftp.mkdir(d)
            except IOError:
                pass  # 可能已存在

    def _rmdir_recursive(self, sftp, remote_dir: str) -> None:
        """递归删除远程目录"""
        for entry in sftp.listdir_attr(remote_dir):
            path = f"{remote_dir}/{entry.filename}"
            is_dir = stat.S_ISDIR(entry.st_mode) if entry.st_mode else False
            if is_dir:
                self._rmdir_recursive(sftp, path)
            else:
                sftp.remove(path)
        sftp.rmdir(remote_dir)

    def _list_remote_recursive(self, sftp, remote_dir: str) -> List[Dict]:
        """递归列出远程目录所有文件"""
        result = []
        for entry in sftp.listdir_attr(remote_dir):
            path = f"{remote_dir}/{entry.filename}"
            is_dir = stat.S_ISDIR(entry.st_mode) if entry.st_mode else False
            if is_dir:
                result.extend(self._list_remote_recursive(sftp, path))
            else:
                result.append({
                    "path": path,
                    "size": entry.st_size or 0,
                })
        return result
