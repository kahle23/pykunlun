"""
上下文模块。

提供上下文抽象与「当前值」容器的通用原语：

  - :class:`Context`：上下文抽象基类（约定通用键值存储）。
  - :class:`ContextHolder`：基于 ``contextvars`` 的泛型「当前值」容器，封装
    get/set/reset/using，按线程/asyncio Task 隔离。

本模块只提供通用原语，不含任何 CLI 专有内容，也不持有「当前上下文」全局态——
具体上下文形态（如命令行的 :class:`~pykunlun.core.cli.CliContext`）及其对应的
``ContextHolder`` 实例由上层模块（如 :mod:`pykunlun.core.cli`）按需定义。
"""
from __future__ import annotations

import contextlib
import contextvars
from abc import ABC, abstractmethod
from collections.abc import Generator, MutableMapping
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class Context(ABC):
    """
    上下文抽象基类。

    具体形态（如命令行上下文 ``CliContext``）由上层模块继承提供。基类只约定一个
    通用键值存储，供跨切面状态挂载。
    """

    @abstractmethod
    def get_storage(self) -> MutableMapping[str, Any]:
        """
        获取通用键值存储。

        供跨切面状态挂载（如结果分隔符、输出编码等），具体键由上层约定。

        Returns:
            存储映射（MutableMapping）。
        """
        ...


class ContextHolder(Generic[T]):
    """
    泛型「当前值」容器：封装一个 ``ContextVar[T | None]``，提供 get/set/reset/using。

    每个实例对应一个独立的「当前 X」槽位（按线程、asyncio Task 各自隔离，互不串扰）。
    上层按需 new 出自己的 holder——例如 :mod:`pykunlun.core.cli` 用它承载「当前
    CliContext」。

    线程/异步安全由 ``contextvars`` 保证。

    典型用法::

        holder = ContextHolder[MyType]("my_name")
        with holder.using(value):
            ...holder.get() 拿到 value；出了 with 自动还原...
    """

    def __init__(self, name: str, default: T | None = None) -> None:
        """
        构造容器。

        Args:
            name: 内部 ``ContextVar`` 的名字，用于调试/回溯（多库共存时便于区分）。
            default: 未 set 时的默认值，默认 None。
        """
        self._var: contextvars.ContextVar[T | None] = contextvars.ContextVar(
            name, default=default
        )

    @property
    def name(self) -> str:
        """内部 ``ContextVar`` 的名字。"""
        return self._var.name

    def get(self) -> T | None:
        """
        获取当前值。

        Returns:
            当前值；未 set（或已 reset）时返回 ``default``（默认 None）。
        """
        return self._var.get()

    def set(self, value: T) -> contextvars.Token[T | None]:
        """
        设置当前值。

        通常与 :meth:`reset` 在 try/finally 中成对使用；多数场景直接用 :meth:`using`
        更省心（自动还原、无需感知 Token）。

        Args:
            value: 要设为当前的值。

        Returns:
            Token，需原样交给 :meth:`reset` 以恢复到设置前的值。
        """
        return self._var.set(value)

    def reset(self, token: contextvars.Token[T | None]) -> None:
        """
        将当前值恢复到对应 :meth:`set` 之前的状态。

        Args:
            token: :meth:`set` 返回的 Token。
        """
        self._var.reset(token)

    @contextlib.contextmanager
    def using(self, value: T) -> Generator[T, None, None]:
        """
        上下文管理器：在 with 块内把 ``value`` 设为当前值，退出时自动还原。

        内部封装 :meth:`set` / :meth:`reset`，调用方无需感知 Token，且保证异常时
        也能还原。推荐优先使用本方法。

        Example::

            with holder.using(value):
                do_work()   # holder.get() 拿到 value；出了 with 自动还原

        Args:
            value: with 块期间生效的值。

        Yields:
            传入的 value 本身，方便 ``with holder.using(v) as x:`` 写法。
        """
        token = self._var.set(value)
        try:
            yield value
        finally:
            self._var.reset(token)
