"""
CLI 命令行框架。

提供命令系统的核心抽象与运行时：上下文、命令基类、命令管理器、
帮助命令等，按子模块组织。

  - :mod:`pykunlun.cli.context`：命令行上下文与「当前上下文」槽位；
  - :mod:`pykunlun.cli.command`：命令抽象基类、内置帮助命令与异常；
  - :mod:`pykunlun.cli.manager`：命令注册器与命令行入口。
"""

from .command import Command, CommandNotFoundError, HelpCommand
from .context import CliContext, cli_context_holder
from .manager import CommandManager

__all__ = [
    'CliContext',
    'Command',
    'CommandManager',
    'CommandNotFoundError',
    'HelpCommand',
    'cli_context_holder',
]
