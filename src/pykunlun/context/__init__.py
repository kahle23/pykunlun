"""
上下文（Context）能力包。

提供上下文的通用原语：契约与「当前值」容器，按子模块组织。

  - :mod:`pykunlun.context.base`：上下文抽象基类（约定通用键值存储）；
  - :mod:`pykunlun.context.holder`：泛型「当前值」容器（contextvars 封装）。

本包不含任何 CLI 专有内容，也不持有「当前上下文」全局态——具体上下文形态
（如命令行的 :class:`~pykunlun.cli.CliContext`）及其对应的 ``ContextHolder``
实例由上层包（如 :mod:`pykunlun.cli`）按需定义。
"""

from .base import Context
from .holder import ContextHolder

__all__ = [
    'Context',
    'ContextHolder',
]
