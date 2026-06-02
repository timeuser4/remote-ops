"""Agent 异常层次结构"""


class AgentError(Exception):
    """所有 agent 异常的基类"""
    pass


class ConnectionError(AgentError):
    """SSH 连接失败（网络、主机不可达、超时）"""
    pass


class AuthError(AgentError):
    """SSH 认证失败（密码错误、密钥被拒绝）"""
    pass


class TmuxError(AgentError):
    """远程 tmux 操作失败（会话不存在、安装失败）"""
    pass


class ConfigError(AgentError):
    """本地配置错误（连接未找到、配置损坏）"""
    pass


class KeyGenerationError(AgentError):
    """SSH 密钥对生成失败"""
    pass
