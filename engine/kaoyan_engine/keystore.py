"""API Key 存取：优先使用系统凭据管理器（Windows 凭据管理器 / macOS 钥匙串），其次读取环境变量。

Key 永远不写入 config.json 或数据库。
"""

from __future__ import annotations

import os

import keyring
from keyring.errors import KeyringError

SERVICE = "kaoyan-agent"


class KeyStore:
    def __init__(self, env_names: dict[str, str] | None = None) -> None:
        self.env_names = env_names or {}
        self._memory: dict[str, str] = {}

    def get(self, provider_id: str) -> str | None:
        if provider_id in self._memory:
            return self._memory[provider_id]
        try:
            value = keyring.get_password(SERVICE, provider_id)
        except (KeyringError, RuntimeError):
            value = None
        if value:
            return value
        env = self.env_names.get(provider_id)
        return os.environ.get(env) if env else None

    def set(self, provider_id: str, key: str) -> bool:
        """返回 True 表示已存入系统凭据管理器；False 表示当前系统没有可用的凭据后端，只在本次运行期间有效。"""
        try:
            if key:
                keyring.set_password(SERVICE, provider_id, key)
            else:
                keyring.delete_password(SERVICE, provider_id)
            self._memory.pop(provider_id, None)
            return True
        except (KeyringError, RuntimeError):
            if key:
                self._memory[provider_id] = key
            else:
                self._memory.pop(provider_id, None)
            return False

    def has(self, provider_id: str) -> bool:
        return bool(self.get(provider_id))
