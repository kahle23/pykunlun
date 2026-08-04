"""
核心抽象层模块。

提供上下文、动作注册等通用抽象原语，按子模块组织。
各抽象的具体实现由上层包提供。
"""

from . import action
from .ctxt import Context, ContextHolder

__all__ = [
    'Context',
    'ContextHolder',
    'action',
]
