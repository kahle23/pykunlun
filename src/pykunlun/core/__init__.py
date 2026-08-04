"""
核心抽象层模块。

提供命令系统、动作注册等通用抽象接口与注册机制，按子模块组织。
各抽象的具体实现由上层包提供。
"""

from . import action, cli
from .cli import (
    CliContext,
    Command,
    CommandManager,
    CommandNotFoundError,
    HelpCommand,
    context_holder,
)
from .ctxt import Context, ContextHolder

__all__ = [
    'CliContext',
    'Command',
    'CommandManager',
    'CommandNotFoundError',
    'Context',
    'ContextHolder',
    'HelpCommand',
    'action',
    'cli',
    'context_holder',
]
