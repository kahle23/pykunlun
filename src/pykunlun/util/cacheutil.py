"""
缓存门面模块：默认实例 + ``@cached`` 装饰器 + 统一管理门面。

调用方常见两种用法：

  - **装饰函数结果**（最常用）：``@cacheutil.cached(ttl=60)`` 自动缓存返回值，支持 TTL、
    "``None`` 不缓存"、自定义 key；
    >>> @cached(ttl=60)
    ... def get_user(uid):
    ...     ...
  - **直接用缓存实例**：``cacheutil.default_cache().put('k', v, ttl=60)``，
    或通过 :data:`cache_manager` 取用命名缓存。

抽象与内置实现（:class:`Cache` / :class:`MemoryCache` / :class:`CacheManager`）见
:mod:`pykunlun.data.cache`；本模块提供默认 :data:`cache_manager`、:data:`default_cache`
单例、:func:`cached` 装饰器，以及转发到 :data:`cache_manager` 的便捷函数。
"""

from collections.abc import Callable
from functools import wraps
from typing import Any, ParamSpec, TypeVar, cast

from pykunlun.data.cache import Cache, CacheManager, MemoryCache

P = ParamSpec('P')
R = TypeVar('R')

# 缺省 key 未命中哨兵：用模块级单例 object 区分"未命中"与"缓存了 None"。
_MISS: Any = object()


# region ======== 默认实例 ========

#: 默认缓存管理器（命名缓存注册表）。
cache_manager = CacheManager()

#: 默认缓存实例（纯内存，已注册到 :data:`cache_manager`，名 ``'default'``）。
default_cache: Cache[Any, Any] = MemoryCache(name='default')
cache_manager.register_cache(default_cache)


def get_cache(name: str) -> Cache[Any, Any] | None:
    """按名取已注册缓存（转发 :data:`cache_manager`）。"""
    return cache_manager.get_cache(name)


def register_cache(cache: Cache[Any, Any]) -> Cache[Any, Any] | None:
    """
    登记一个缓存（转发）。

    Returns:
        被覆盖的旧缓存；无旧值时为 ``None``。
    """
    return cache_manager.register_cache(cache)


def unregister_cache(name: str) -> Cache[Any, Any] | None:
    """
    注销缓存（转发）。

    Returns:
        被移除的缓存；不存在时为 ``None``。
    """
    return cache_manager.unregister_cache(name)


def get_cache_names() -> list[str]:
    """列出已注册缓存名（转发，按名升序）。"""
    return cache_manager.get_cache_names()


def clear_all() -> None:
    """清空所有已注册缓存的内容（转发，便于运维 / 单测一键重置）。"""
    cache_manager.clear_all()

# endregion


# region ======== @cached 装饰器 ========

def _default_key(*args: Any, **kwargs: Any) -> Any:
    """
    默认 key 构造：``(args, tuple(sorted(kwargs.items())))``。

    要求位置参数与关键字参数的值都可哈希；含不可哈希参数时调用方需自行传 *key_builder*。
    """
    return (args, tuple(sorted(kwargs.items())))


def cached(
    ttl: float | None = None,
    *,
    cache: Cache[Any, Any] | None = None,
    cacheable: Callable[[Any], bool] | None = None,
    key_builder: Callable[..., Any] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """
    函数结果缓存装饰器。

    被装饰函数每次调用：依 *key_builder*（默认基于 ``args`` + 排序后的 ``kwargs``）算出 key，
    到 *cache*（默认为该函数新建独立 :class:`MemoryCache`）查；命中则直接返回，未命中则执行
    原函数，结果经 *cacheable* 判定（默认都缓存）后回填缓存。

    关于 ``None`` 返回值：装饰器内部用哨兵调 :meth:`Cache.get`，因此即便原函数合法返回
    ``None``，也能与"未命中"正确区分、被缓存（命中后不再重复执行原函数）。若希望"返回 ``None``
    时不缓存、下次仍重试"，传 ``cacheable=lambda v: v is not None``。

    Args:
        ttl: 每条结果的有效期秒数。``None`` 表示永久；``0`` 同样永久；正数为该秒数。
        cache: 指定使用的 :class:`Cache` 实例；为 ``None`` 时为该函数新建独立 ``MemoryCache``
            （各被装饰函数互不影响，且其 ``default_ttl`` 取本参数 *ttl*）。
        cacheable: 值是否值得缓存；为 ``None`` 表示都缓存（含 ``None``）。
            典型用法 ``cacheable=lambda v: v is not None`` 实现"``None`` 不缓存"。
        key_builder: 自定义 ``(func 的 *args, **kwargs) -> 可哈希 key``；为 ``None`` 用默认。
            含不可哈希参数（如 list/dict）的函数需自行提供。

    Returns:
        装饰器。被装饰后函数附带一个 ``cache`` 属性，指向实际使用的 :class:`Cache` 实例，
        便于检查 :meth:`Cache.stats` 或手动 :meth:`Cache.clear`。

    Examples:
        >>> @cached(ttl=60)
        ... def get_user(uid):
        ...     return db.find(uid)
        >>> @cached(ttl=1800, cacheable=lambda v: v is not None)
        ... def lookup(name):
        ...     return maybe_none(name)   # 返回 None 时不缓存，下次仍重新执行
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        used_cache: Cache[Any, Any] = (
            cache if cache is not None
            else MemoryCache(
                name=f'@cached:{func.__module__}.{func.__qualname__}',
                default_ttl=ttl,
            )
        )
        kb = key_builder if key_builder is not None else _default_key

        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            key = kb(*args, **kwargs)
            hit = used_cache.get(key, _MISS)
            if hit is not _MISS:
                return cast('R', hit)
            val = func(*args, **kwargs)
            if cacheable is None or cacheable(val):
                used_cache.put(key, val, ttl)
            return val

        # 暴露内部 cache 便于外部检查 stats / 手动清理
        wrapper.cache = used_cache  # type: ignore[attr-defined]
        return wrapper

    return decorator

# endregion
