"""
缓存抽象层与内置实现。

本模块定义缓存存取的统一抽象，并内置一个纯内存实现：

  - :class:`Cache`：缓存抽象基类（泛型 ``Cache[K, V]``：键类型 ``K``、值类型 ``V``），
    定义 ``get`` / ``put`` / ``remove`` / ``contains_key`` / ``size`` / ``clear``
    等存取语义，以及 compute-if-absent 便捷入口 :meth:`Cache.compute_if_absent`；
  - :class:`MemoryCache`：默认的纯内存实现（``OrderedDict`` + ``RLock``，支持 TTL 过期、
    LRU 淘汰与命中统计，线程安全）；
  - :class:`CacheManager`：命名缓存注册表（按名登记 / 取用 / 统一清空）。

ABC 仅规定 **存取语义**，不规定存储介质、序列化方式、淘汰策略——这些都由各实现类
自己的构造参数决定。基于 Redis / 磁盘（sqlite）等外部存储的实现由上层包提供
（同 :mod:`pykunlun.db.rdb` 的策略模式），实现同一个 ``Cache[K, V]`` 接口即可被
:class:`CacheManager` 统一管理。各操作与远程存储的映射概览：

  - ``put(k, v, ttl)`` → ``SET k v EX ttl``（Redis 服务端自动过期，无需 lazy 清理）；
  - ``get`` / ``remove`` / ``contains_key`` → ``GET`` / ``DEL`` / ``EXISTS``；
  - ``clear()`` / ``size()`` → 按 key 前缀 ``SCAN+DEL`` / 计数（非 O(1)，由实现承担）；
  - 序列化、``maxsize`` 等均为实现构造参数，不进 ABC 契约。

关于 ``None`` 值：本抽象沿用"未命中返回 ``None``"的缓存通用约定，因此 **缓存中存入
``None`` 与未命中无法区分**。需要区分时用 :meth:`Cache.get` 的 ``default`` 传哨兵
（见 :func:`pykunlun.util.cacheutil.cached` 装饰器的实现），或在调用层约定不缓存 ``None``。

默认管理器实例、``@cached`` 装饰器与对外门面由 :mod:`pykunlun.util.cacheutil` 提供。

仅依赖 Python 标准库。
"""

import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Generic, TypeVar, cast

#: 缓存的键类型参数。子类/实现通过 ``Cache[str, User]`` 等声明自己接受的键类型；
#: 在 :class:`CacheManager` 注册表边界擦除为 ``Cache[Any, Any]``（等同 Java 的 ``Cache<?,?>``）。
K = TypeVar('K')
#: 缓存的值类型参数。同 :data:`K`，绑定 ``get`` 的返回与 ``put`` 的入参。
V = TypeVar('V')


# region ======== 缓存抽象基类 ========

class Cache(ABC, Generic[K, V]):
    """
    缓存抽象基类（泛型 ``Cache[K, V]``）。

    子类需实现 6 个抽象方法：:meth:`get` / :meth:`put` / :meth:`remove` /
    :meth:`contains_key` / :meth:`size` / :meth:`clear`；本类据此额外提供：

      - :meth:`compute_if_absent`：compute-if-absent 的便捷入口（默认实现基于 :meth:`get`
        + :meth:`put`，命中即返回，未命中执行 loader 回填）；
      - :meth:`stats`：统计 hook，默认返回空 dict，:class:`MemoryCache` 等实现按能力覆写；
      - :attr:`name` 标识、:meth:`__repr__`。

    ABC 仅规定存取语义，**不**规定存储介质、序列化方式、淘汰策略：

      - **TTL（每条目过期）**：由 :meth:`put` 的 ``ttl`` 参数表达；实现可像
        :class:`MemoryCache` 那样在 :meth:`get` 时 lazy 清理，也可像未来的 Redis 实现
        那样交给服务端自动过期；
      - **容量上限 / 淘汰**：由实现类的构造参数决定（如 :class:`MemoryCache` 的 ``maxsize``），
        不进 ABC 契约；
      - **值的可序列化性**：:class:`MemoryCache` 接受任意 Python 对象；远程存储实现
        （Redis 等）要求 ``V`` 可被其序列化器处理——此约束由具体实现隐式承担，ABC 不强制。

    Attributes:
        name: 缓存名（用于 :class:`CacheManager` 注册、日志、:meth:`__repr__`）。
    """

    def __init__(self, name: str | None = None) -> None:
        """
        Args:
            name: 缓存名；为空或纯空白时抛出。

        Raises:
            ValueError: name 为空或纯空白时抛出。
        """
        if not name or not name.strip():
            raise ValueError("缓存名 name 不能为空！")
        self.name = name.strip()

    def __repr__(self) -> str:
        """
        返回开发调试用的对象字符串表示（含类名与缓存名）。
        """
        return f'<{type(self).__name__} name={self.name!r}>'

    @abstractmethod
    def get(self, key: K, default: V | None = None) -> V | None:
        """
        取值；未命中或已过期返回 *default*（默认 ``None``）。

        注意：缓存中存入 ``None`` 与未命中无法区分（都表现为返回 ``None``）。
        需区分时传一个哨兵作为 *default*，由调用方比对哨兵判定是否命中。

        Args:
            key: 缓存键。
            default: 未命中时的返回值（默认 ``None``）。

        Returns:
            命中的缓存值；未命中或已过期返回 *default*。
        """
        raise NotImplementedError

    @abstractmethod
    def put(self, key: K, value: V, ttl: float | None = None) -> V | None:
        """
        存值；返回被覆盖的旧值（未命中为 ``None``）。

        Args:
            key: 缓存键。
            value: 缓存值。
            ttl: 该条目的有效期（秒）。``None`` 表示沿用实现的默认 TTL（:class:`MemoryCache`
                为构造时的 ``default_ttl``）；``0`` 或正数为该秒数（``0`` 强制永久，即使实现
                有默认 TTL）。远程存储实现按各自语义处理。

        Returns:
            被覆盖的旧值；未命中时为 ``None``。
        """
        raise NotImplementedError

    @abstractmethod
    def remove(self, key: K) -> V | None:
        """
        删除条目；返回被删的旧值（未命中为 ``None``）。
        """
        raise NotImplementedError

    @abstractmethod
    def contains_key(self, key: K) -> bool:
        """
        判断键是否存在且未过期。

        已过期的条目会被顺带清理（lazy 过期）。键需可哈希。
        """
        raise NotImplementedError

    @abstractmethod
    def size(self) -> int:
        """
        返回当前条目数。

        注意：lazy 过期策略下，返回值可能略大于实际有效条目数（未被任何 :meth:`get` /
        :meth:`contains_key` 触及的过期项尚未清理）。
        """
        raise NotImplementedError

    @abstractmethod
    def clear(self) -> None:
        """
        清空所有条目。
        """
        raise NotImplementedError

    def compute_if_absent(self, key: K, loader: Callable[[K], V],
                    ttl: float | None = None) -> V:
        """
        compute-if-absent：未命中（含过期）时调用 *loader* 计算值、回填缓存后返回；
        命中则直接返回缓存值。

        用哨兵调 :meth:`get` 以正确区分"未命中"与"缓存了 ``None``"两种情况——即便 loader
        合法返回 ``None``，下次调用也不会重复执行 loader。

        注意：本默认实现 **不** 在锁内调用 loader，避免 loader 回调访问缓存造成死锁、
        也避免长耗时的 loader 阻塞其他读。因此并发场景下两个调用方可能同时 miss 并都
        执行 loader——进程内（:class:`MemoryCache`）已通过实现内部的锁降低概率，跨进程
        （多实例共用一个远程缓存）的"惊群"由具体实现自行加分布式锁处理。

        Args:
            key: 缓存键。
            loader: 未命中时的取值回调（接收 key，返回 ``V``）。
            ttl: 回填时该条目的有效期秒数；``None`` 沿用实现的默认 TTL。

        Returns:
            命中的缓存值，或 loader 计算出的值。
        """
        sentinel: Any = object()
        v = self.get(key, sentinel)
        if v is not sentinel:
            return v  # type: ignore[return-value]
        v = loader(key)
        self.put(key, v, ttl)
        return v

    def stats(self) -> dict[str, Any]:
        """
        返回缓存统计信息（命中 / 未命中 / 淘汰 / 过期等，由实现按能力填充）。

        默认返回空 dict；:class:`MemoryCache` 覆写为 hits / misses / evictions / expired / size。
        远程存储实现可用各自手段（如 Redis 的 ``INCR`` 计数）填充同结构。
        """
        return {}

# endregion


# region ======== 内置实现：纯内存缓存 ========

class MemoryCache(Cache[K, V]):
    """
    纯内存缓存实现（``OrderedDict`` + ``RLock``，线程安全）。

    支持：

      - **TTL**：构造时设 ``default_ttl``（秒）作为默认有效期；单条 :meth:`put` 可传
        ``ttl`` 覆盖（``None`` 沿用默认，``0`` 强制永久，正数为该秒数）；
      - **LRU 淘汰**：构造时设 ``maxsize``；超出时淘汰最久未访问者（``popitem(last=False)``）。
        ``maxsize=None`` 表示无上限（仍受 TTL 过期约束）；
      - **lazy 过期**：:meth:`get` / :meth:`contains_key` 检查并清理命中条目的过期；
        不做后台定期扫描（简单可靠，足够多数场景）；
      - **统计**：:meth:`stats` 返回 hits / misses / evictions / expired / size。

    泛型：``MemoryCache[str, User]()`` 等。键需可哈希。

    线程安全：所有存取方法在 ``RLock`` 内完成；:meth:`compute_if_absent` 的 loader 回调
    在锁外执行（避免死锁），代价是并发下可能重复执行 loader。
    """

    def __init__(self, name: str | None = None,
                 maxsize: int | None = None,
                 default_ttl: float | None = None) -> None:
        """
        Args:
            name: 缓存名（注册到 :class:`CacheManager` 的键）；为空抛错。
            maxsize: 容量上限；超出按 LRU 淘汰。``None`` 表示无上限。
            default_ttl: 默认有效期（秒），单条 :meth:`put` 未显式传 ``ttl`` 时使用。
                ``None`` 表示永久。

        Raises:
            ValueError: name 为空、maxsize<=0、或 default_ttl 为负时抛出。
        """
        super().__init__(name)
        if maxsize is not None and maxsize <= 0:
            raise ValueError(f"maxsize 必须为正整数或 None，实际: {maxsize}")
        if default_ttl is not None and default_ttl < 0:
            raise ValueError(f"default_ttl 不能为负，实际: {default_ttl}")
        self._maxsize = maxsize
        self._default_ttl = default_ttl
        # store[key] = (value, expire_at | None)；访问命中时 move_to_end 维护 LRU
        self._store: OrderedDict[Any, tuple[Any, float | None]] = OrderedDict()
        self._lock = threading.RLock()
        # 统计计数（粗略，非精确并发安全——仅用于可观测）
        self._hits = 0
        self._misses = 0
        self._evictions = 0  # 因 maxsize 被淘汰
        self._expired = 0    # 因 TTL 过期被清理

    @staticmethod
    def _now() -> float:
        # 用 monotonic 免受系统时钟回拨影响
        return time.monotonic()

    def _expire_at_of(self, ttl: float | None) -> float | None:
        """根据 put 入参 ttl 计算过期时间戳；返回 None 表示永久。"""
        # ttl=None 沿用默认；0 / None / False 视为永久；正数为该秒数
        effective = self._default_ttl if ttl is None else ttl
        return (self._now() + effective) if effective else None

    def get(self, key: K, default: V | None = None) -> V | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return default
            value, expire_at = entry
            if expire_at is not None and self._now() >= expire_at:
                # lazy 清理过期项
                self._store.pop(key, None)
                self._expired += 1
                self._misses += 1
                return default
            # 命中：维护 LRU 顺序
            self._store.move_to_end(key)
            self._hits += 1
            return cast('V | None', value)

    def put(self, key: K, value: V, ttl: float | None = None) -> V | None:
        with self._lock:
            old = self._store.get(key)
            old_value = cast('V | None', old[0]) if old is not None else None
            self._store[key] = (value, self._expire_at_of(ttl))
            self._store.move_to_end(key)  # 写入也算访问，置于最新端
            # 容量淘汰（仅当新增 key 时触发；覆盖不增量）
            if (old is None and self._maxsize is not None
                    and len(self._store) > self._maxsize):
                self._store.popitem(last=False)  # 淘汰最久未访问
                self._evictions += 1
            return old_value

    def remove(self, key: K) -> V | None:
        with self._lock:
            entry = self._store.pop(key, None)
            return cast('V | None', entry[0]) if entry is not None else None

    def contains_key(self, key: K) -> bool:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return False
            if entry[1] is not None and self._now() >= entry[1]:
                self._store.pop(key, None)
                self._expired += 1
                return False
            return True

    def size(self) -> int:
        # 不主动扫描全部过期项（O(n) 过重）；返回当前存储条目数，可能略大于实际有效数。
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                'hits': self._hits,
                'misses': self._misses,
                'evictions': self._evictions,
                'expired': self._expired,
                'size': len(self._store),
            }

# endregion


# region ======== 命名缓存注册表 ========

class CacheManager:
    """
    命名缓存注册表：按名登记 :class:`Cache` 实例，集中取用与统一清空。

    典型用法：应用各处用不同 ``name`` 注册各自的缓存（如 ``'metadata'``、``'user'``），
    运维 / 单测通过 :meth:`clear_all` 一键清空全部，或 :meth:`get_cache` 跨模块取用
    同一实例。

    线程安全（``RLock``）；注册表边界类型擦除为 ``Cache[Any, Any]``。
    """

    def __init__(self) -> None:
        self._caches: dict[str, Cache[Any, Any]] = {}
        self._lock = threading.RLock()

    def _resolve_name(self, name: str) -> str:
        """校验并规范化缓存名：去除前后空格，且不能为空。"""
        stripped = name.strip()
        if not stripped:
            raise ValueError("缓存名 name 不能为空")
        return stripped

    def register_cache(self, cache: Cache[Any, Any]) -> Cache[Any, Any] | None:
        """
        登记一个缓存（按其 :attr:`Cache.name` 存放，允许覆盖同名）。

        Returns:
            被覆盖的旧缓存；无旧值时为 ``None``。

        Raises:
            TypeError: cache 不是 :class:`Cache` 实例时抛出。
            ValueError: cache.name 为空时抛出。
        """
        if not isinstance(cache, Cache):
            raise TypeError(f"cache 必须是 Cache 实例，实际类型: {type(cache)}")
        key = self._resolve_name(cache.name)
        with self._lock:
            old = self._caches.get(key)
            self._caches[key] = cache
            return old

    def unregister_cache(self, name: str) -> Cache[Any, Any] | None:
        """
        注销缓存。

        Returns:
            被移除的缓存；不存在时为 ``None``。
        """
        key = self._resolve_name(name)
        with self._lock:
            return self._caches.pop(key, None)

    def get_cache(self, name: str) -> Cache[Any, Any] | None:
        """
        按名取已注册缓存（不执行清空等操作）。

        Returns:
            命中的缓存实例；不存在时为 ``None``。
        """
        key = self._resolve_name(name)
        with self._lock:
            return self._caches.get(key)

    def has_cache(self, name: str) -> bool:
        """判断缓存是否已注册。"""
        key = self._resolve_name(name)
        with self._lock:
            return key in self._caches

    def get_cache_names(self) -> list[str]:
        """列出已注册缓存名（按名称升序）。"""
        with self._lock:
            return sorted(self._caches.keys())

    def clear_all(self) -> None:
        """
        清空所有已注册缓存的内容（不注销注册表本身）。

        用于运维 / 单测一键重置全部缓存。
        """
        with self._lock:
            caches = list(self._caches.values())
        for c in caches:
            c.clear()

# endregion
