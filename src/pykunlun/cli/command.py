"""
命令抽象与内置实现模块。

提供命令基类 :class:`Command`、内置帮助命令 :class:`HelpCommand` 以及
命令未找到异常 :class:`CommandNotFoundError`。
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pykunlun.cli.context import CliContext

if TYPE_CHECKING:
    from pykunlun.cli.manager import CommandManager


class Command(ABC):
    """
    命令基类。

    所有命令都需要继承此基类并实现必要的抽象方法，
    提供统一的命令接口和默认行为。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """
        命令名称（不含 ``--`` 前缀，如 ``help``；理论上亦可含，由调用方约定）。
        """
        pass

    @property
    def abbr(self) -> str | None:
        """
        命令缩写（不含 ``-`` 前缀，如 ``h``；未设置时返回 None）。
        """
        return None

    @property
    @abstractmethod
    def description(self) -> str:
        """
        获取命令描述。
        """
        pass

    @property
    def usage(self) -> str:
        """
        获取命令用法示例。
        """
        return f"{self.name} [参数1] [参数2] ..."

    @abstractmethod
    def execute(self, ctx: CliContext) -> Any:
        """
        执行命令。

        Args:
            ctx: 当前命令行上下文。命令自身参数经 ``ctx.current_args`` 获取
                （已剥离 ``--delim`` 等全局标记、且命令名已被消费）；
                结果分隔符等跨切面信息也从 ctx 读取（如 ``ctx.print_delim()``）。

        Returns:
            命令执行结果（任意类型）。
        """
        pass

    def show_usage(self) -> None:
        """
        显示命令用法。
        """
        print(f"用法: {self.usage}")


class HelpCommand(Command):
    """
    帮助命令。

    提供帮助命令的默认实现。开发者可继承此类并重写 :meth:`single_help_text` /
    :meth:`full_help_text` 方法自定义帮助信息。未继承时使用默认帮助信息格式。
    """

    def __init__(self, manager: 'CommandManager') -> None:
        """
        初始化帮助命令。

        Args:
            manager: 命令管理器实例，用于获取已注册的命令。
        """
        self._manager = manager

    @property
    def name(self) -> str:
        return "help"

    @property
    def abbr(self) -> str:
        return "h"

    @property
    def description(self) -> str:
        return "显示帮助信息"

    @property
    def usage(self) -> str:
        return f"{self.name} [命令]"

    def single_help_text(self, command: Command) -> str:
        """
        生成单个命令的帮助文本。

        Args:
            command: 命令实例。

        Returns:
            str: 生成的帮助文本。
        """
        return (
            f"命令: {command.name}\n"
            f"描述: {command.description}\n"
            f"用法: {command.usage}"
        )

    def full_help_text(self, commands: dict[str, Command]) -> str:
        """
        生成所有命令的帮助文本。

        Args:
            commands: 已注册的命令字典，键为命令名称，值为命令实例。

        Returns:
            str: 生成的帮助文本。
        """
        # 构建命令列表
        command_lines = []
        for cmd in commands.values():
            command_lines.append(f"    {cmd.name:<12} {cmd.description}")
        commands_text = "\n".join(command_lines)
        # 构建帮助文本
        return (
            f"可用命令:\n"
            f"{commands_text}\n\n"
            f"使用 {self.usage} 查看具体命令的详细用法"
        )

    def execute(self, ctx: CliContext) -> Any:
        # ctx.current_args 已不含命令名（被 command_name_parse 消费）；它是 help 的参数
        args = ctx.current_args
        # 如果指定了命令，显示具体命令的帮助
        if args:
            command_name = args[0]
            command = self._manager.get_command(command_name)
            if command:
                print(self.single_help_text(command))
                return True
            else:
                print(f"未知命令: {command_name}")
                return False
        # 否则显示所有命令
        commands = self._manager.get_all_commands()
        print(self.full_help_text(commands))
        return True


class CommandNotFoundError(Exception):
    """
    命令未找到异常。

    当尝试执行未注册的命令时抛出此异常。
    """
    pass
