"""本地配置管理，存储在 ~/.remote-ops/ 下"""

import json
from pathlib import Path
from typing import Optional, Dict, List

from .exceptions import ConfigError


class AgentConfig:
    """管理 agent 的本地持久化配置"""

    AGENT_DIR: Path = Path.home() / ".remote-ops"
    CONNECTIONS_FILE: Path = AGENT_DIR / "connections.json"

    @classmethod
    def initialize(cls) -> None:
        """创建 ~/.remote-ops/ 目录（如果不存在）"""
        cls.AGENT_DIR.mkdir(parents=True, exist_ok=True)

    @classmethod
    def _load(cls) -> Dict:
        """加载配置文件"""
        if not cls.CONNECTIONS_FILE.exists():
            return {"connections": {}}
        try:
            with open(cls.CONNECTIONS_FILE, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            raise ConfigError(f"配置文件损坏: {e}")

    @classmethod
    def _save(cls, data: Dict) -> None:
        """保存配置文件"""
        cls.initialize()
        with open(cls.CONNECTIONS_FILE, 'w') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @classmethod
    def save_connection(
        cls,
        host_alias: str,
        hostname: str,
        username: str,
        ip_address: str,
        key_path: str,
        port: int = 22,
        via: Optional[str] = None,
    ) -> None:
        """
        保存连接配置

        如果别名已存在则覆盖
        """
        data = cls._load()
        conn = {
            "hostname": hostname,
            "username": username,
            "ip_address": ip_address,
            "key_path": key_path,
            "port": port,
        }
        if via:
            conn["via"] = via
        data["connections"][host_alias] = conn
        cls._save(data)

    @classmethod
    def get_connection(cls, host_alias: str) -> Optional[Dict]:
        """按别名获取连接配置，未找到返回 None"""
        data = cls._load()
        return data.get("connections", {}).get(host_alias)

    @classmethod
    def list_connections(cls) -> List[Dict]:
        """列出所有已保存的连接"""
        data = cls._load()
        connections = data.get("connections", {})
        result = []
        for alias, info in connections.items():
            result.append({
                "alias": alias,
                **info,
            })
        return result

    @classmethod
    def remove_connection(cls, host_alias: str) -> bool:
        """移除已保存的连接，找到并删除返回 True，否则返回 False"""
        data = cls._load()
        connections = data.get("connections", {})
        if host_alias in connections:
            del connections[host_alias]
            cls._save(data)
            return True
        return False
