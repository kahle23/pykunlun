"""
环境变量管理的底层抽象模块，定义策略接口与实例注册表。

采用策略模式：
  - :class:`EnvVarService` 为抽象基类，定义跨平台的环境变量管理接口；
  - 各平台具体实现（如 Windows、Unix-like）由上层包提供；
  - 通过 :class:`EnvVarManager` 维护平台注册表，并依据当前操作系统
    自动选择对应实现。

本模块仅提供抽象与注册表，**不内置任何平台实现**，也不依赖上层包。
上层包需在导入时通过 :meth:`EnvVarManager.register_service`
注册各平台的具体实现。

仅依赖 Python 标准库。
"""

import os
import platform as _platform
from abc import ABC, abstractmethod

# 环境变量写入范围常量（set_var 的 scope 参数取值）
SCOPE_SYSTEM = 1  # 系统级环境变量
SCOPE_USER = 2    # 用户级环境变量


# region ======== 策略抽象基类 ========

class EnvVarService(ABC):
    """
    环境变量管理策略抽象基类。

    各平台（Windows、Unix-like）需继承此类并实现平台特有方法，
    并通过构造函数指定 ``platform`` 实例属性，便于细分平台的子类按实例定制。
    所有写入操作均应保证永久生效（Windows 写入注册表；Unix 写入 shell rc 文件），
    并同步到当前进程环境。
    """

    def __init__(self, platform: str) -> None:
        """
        Args:
            platform: 平台标识（小写），如 ``windows``、``unix``，可由子类给定默认值。
        """
        self.platform = platform

    @abstractmethod
    def set_var(self, name: str, value: str, scope: int | None = None) -> bool:
        """
        设置环境变量（永久生效）。

        环境变量分为用户级与系统级，通过 ``scope`` 指定写入范围：

          - :data:`SCOPE_SYSTEM` (1)：系统级环境变量；
          - :data:`SCOPE_USER` (2)：用户级环境变量；
          - ``None``：缺省范围，具体默认值（用户或系统）由各平台实现决定。

        Args:
            name: 环境变量名称。
            value: 环境变量值。
            scope: 写入范围（1=系统 / 2=用户），缺省由实现决定。

        Returns:
            设置成功返回 True，失败返回 False。
        """
        pass

    @abstractmethod
    def append_to_path(self, value: str) -> bool:
        """
        将路径值追加到 PATH（永久生效）。

        PATH 中的条目本质上是路径值（目录），本方法直接追加给定的值。

        Args:
            value: 要追加到 PATH 的路径值。

        Returns:
            添加成功返回 True，失败返回 False。
        """
        pass

    @abstractmethod
    def remove_from_path(self, value: str) -> bool:
        """
        从 PATH 中移除指定的路径值（永久生效）。

        Args:
            value: 要从 PATH 移除的路径值。

        Returns:
            移除成功返回 True，失败返回 False。
        """
        pass

    def get_var(self, name: str, default: str = "") -> str:
        """
        获取环境变量（读取当前进程环境）。

        跨平台实现一致，默认从 ``os.environ`` 读取。
        子类如需从持久化存储读取可覆盖。

        Args:
            name: 环境变量名称。
            default: 环境变量不存在时的返回值，默认为空字符串。

        Returns:
            环境变量值，不存在时返回 default。
        """
        return os.environ.get(name, default)

    def get_path_str(self, default: str = "") -> str:
        """
        获取当前 PATH 环境变量的值（读取当前进程环境），以原始字符串形式返回。

        跨平台实现一致，默认从 ``os.environ`` 读取。
        子类如需从持久化存储读取可覆盖。

        Args:
            default: PATH 环境变量不存在时的返回值，默认为空字符串。

        Returns:
            PATH 环境变量值，不存在时返回 default。
        """
        return os.environ.get("PATH", default)


# endregion


# region ======== 环境变量服务管理器（共享注册表） ========

class EnvVarManager:
    """
    环境变量服务管理器（平台注册表）。

    按平台标识（如 ``windows``、``unix``）注册、注销、查找
    :class:`EnvVarService` 实例，并依据当前操作系统自动选择对应实现。

    注册表为实例属性：每个 ``EnvVarManager`` 实例拥有独立的注册表，
    跨实例互不共享。

    注意：本类不内置任何平台实现。上层包需在导入时调用
    :meth:`register_service` 注册各平台实现，否则 :meth:`get_service`
    将因找不到实现而抛出 ``ValueError``。

    用法示例::

        manager = EnvVarManager()

        # 注册自定义平台实现
        manager.register_service("my_platform", MyEnvVarService())

        # 按当前系统自动选择（需已在本实例上注册对应实现）
        svc = manager.get_service()

        # 显式指定平台
        svc = manager.get_service("unix")
    """

    def __init__(self) -> None:
        # 适配器注册表：platform name -> EnvVarService 实例（本实例独有）
        self._registry: dict[str, EnvVarService] = {}

    @staticmethod
    def _current_platform() -> str:
        """获取当前系统的平台标识。"""
        return "windows" if _platform.system().lower() == "windows" else "unix"

    def register_service(self, platform: str, service: EnvVarService) -> None:
        """
        注册或替换指定平台的环境变量管理实例。

        Args:
            platform: 平台标识。
            service: EnvVarService 实例。
        """
        self._registry[platform.lower()] = service

    def unregister_service(self, platform: str) -> bool:
        """
        取消注册指定平台的环境变量管理实例。

        Args:
            platform: 平台标识。

        Returns:
            是否成功移除。
        """
        platform = platform.lower()
        if platform in self._registry:
            del self._registry[platform]
            return True
        return False

    def get_service(self, platform: str | None = None) -> EnvVarService:
        """
        获取指定平台的环境变量管理实例。

        Args:
            platform: 平台标识（如 ``windows``、``unix``），为 None 时自动按当前系统选择。

        Returns:
            EnvVarService 实例。

        Raises:
            ValueError: 不支持的平台、或对应平台尚未注册实现时抛出。
        """
        if platform is None:
            platform = self._current_platform()
        platform = platform.lower()

        service = self._registry.get(platform)
        if not service:
            supported = ", ".join(self._registry.keys()) or "（无）"
            raise ValueError(
                f"不支持的平台: {platform}，已注册的平台: {supported}；"
                f"请先通过 register_service() 注册对应平台的实现"
            )
        return service

    def get_supported_platforms(self) -> list[str]:
        """
        获取所有已注册的平台标识列表。

        Returns:
            平台标识列表。
        """
        return list(self._registry.keys())

# endregion
