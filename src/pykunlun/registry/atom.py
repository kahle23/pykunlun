"""
注册表原子模块。

提供 :class:`Registry`：注册表模式的最小存储构件——线程安全的 ``str -> T``
存取表，只管存取与原子级通用校验（键必须为 str、值不可为 None——None 保留
作未命中哨兵），不涉领域语义与报错文案；"类注册表 / 实例注册表"的领域
语义全部在上层 :class:`~pykunlun.registry.base.RegistryManager`。

上层以两张本原子组合出双层注册表骨架：类表折叠大小写（类型标识口径），
实例表保留大小写（实例别名口径），并共享同一把锁。
"""

import threading
from typing import Generic, TypeVar

TItem = TypeVar('TItem')


class Registry(Generic[TItem]):
    """
    线程安全的 ``str -> T`` 注册表原子（注册表模式的最小存储构件）。

    只负责存取与原子级通用校验：``fold_case=True`` 时所有键先小写折叠再入库
    （类型标识大小写不敏感的口径），``False`` 时保留原样（实例别名区分大小写
    的口径）；键必须为 str、值不可为 None——:meth:`get` 以 ``None`` 为未命中
    哨兵，存入 None 会使"已注册"与"未命中"不可区分。
    不产领域报错文案——那属于调用方的领域语义；
    锁可由构造参数注入以与同管理器的其他表共享（默认自持一把）。
    """

    # lock 注解加引号：threading.RLock 运行时是工厂函数而非类（类型检查器经 typeshed 认可），
    # 引号使该注解不在定义期求值；本模块刻意不用 PEP 563 全局延迟，让其余注解的错误在 import 期暴露
    def __init__(self, *, fold_case: bool = True, lock: 'threading.RLock | None' = None) -> None:
        """
        Args:
            fold_case: 是否把键小写折叠后入库（类型键 ``True``，实例名 ``False``）。
            lock: 外部注入的共享锁；``None`` 时自持一把独立锁。
        """
        self._fold_case = fold_case
        self._lock = lock if lock is not None else threading.RLock()
        self._items: dict[str, TItem] = {}

    def _norm(self, key: str) -> str:
        """
        把键归一为存储形态（按需小写折叠）；非 str 键快速失败。

        Raises:
            TypeError: 键不是字符串时抛出。
        """
        if not isinstance(key, str):
            raise TypeError(f"注册表键必须为 str，收到: {key!r}")
        return key.lower() if self._fold_case else key

    def register(self, key: str, item: TItem, *, replace: bool = True) -> None:
        """
        注册；``replace=True``（默认）时键已存在则覆盖（同既有 manager 的
        替换语义），``False`` 时拒绝覆盖。

        Args:
            key: 注册键，非 str 时抛 :class:`TypeError`。
            item: 注册项；``None`` 不被接受——:meth:`get` 以 ``None`` 为
                未命中哨兵，存入会使"已注册"与"未命中"不可区分。
            replace: 键已存在时是否覆盖；``False`` 且键已存在时抛
                :class:`ValueError`。

        Raises:
            TypeError: ``key`` 不是字符串时抛出。
            ValueError: ``item`` 为 ``None``、或 ``replace=False`` 且键已存在
                时抛出。
        """
        if item is None:
            raise ValueError('注册表不允许注册 None 值（None 保留作未命中哨兵）')
        with self._lock:
            norm = self._norm(key)
            if not replace and norm in self._items:
                raise ValueError(f"键 {norm!r} 已注册，replace=False 不允许覆盖")
            self._items[norm] = item

    def unregister(self, key: str) -> bool:
        """
        注销指定键。

        Returns:
            键存在且已删除为 ``True``；不存在为 ``False``（幂等）。
        """
        with self._lock:
            return self._items.pop(self._norm(key), None) is not None

    def get(self, key: str) -> TItem | None:
        """
        按键查找。

        Returns:
            命中返回对应项；未命中返回 ``None``（报错语义由调用方表达）。
        """
        with self._lock:
            return self._items.get(self._norm(key))

    def keys(self) -> list[str]:
        """
        已注册的键列表（存储形态，即折叠后的键），顺序为注册顺序。
        """
        with self._lock:
            return list(self._items.keys())
