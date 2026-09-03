"""
上下文契约模块。

提供 :class:`Context`：上下文抽象基类，约定通用键值存储。

本模块只提供契约，不含任何 CLI 专有内容，也不持有「当前上下文」全局态——
具体上下文形态（如命令行的 :class:`~pykunlun.cli.CliContext`）由上层模块
（如 :mod:`pykunlun.cli`）继承实现。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import MutableMapping
from typing import Any


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
