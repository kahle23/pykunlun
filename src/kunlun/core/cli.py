"""
命令基类和注册机制模块。

本模块提供命令系统的核心抽象类和注册机制，用于实现可扩展的命令行工具。
"""

import sys
import threading
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from kunlun.envinfo import pkginfo


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
        获取命令名称（如 --xxx，如果需要--，也需要写在此处）。
        """
        pass

    @property
    def abbr(self) -> Optional[str]:
        """
        获取命令缩写（如 -x，如果需要-，也需要写在此处）。

        Returns:
            命令缩写，如果未设置则返回 None。
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
    def execute(self, args: List[str]) -> Any:
        """
        执行命令。

        Args:
            args: 命令参数列表（不包括命令名本身）。

        Returns:
            Any: 命令执行结果，可以是任意类型。
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

    提供帮助命令的默认实现。开发者可以继承此类并重写 generate_help_text 方法来自定义帮助信息。
    如果未继承此类，系统将使用默认的帮助信息格式。
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

    def full_help_text(self, commands: dict) -> str:
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
        # 按命令名称排序
        #command_lines.sort()
        # 拼接命令列表
        commands_text = "\n".join(command_lines)
        # 构建帮助文本
        return (
            f"可用命令:\n"
            f"{commands_text}\n\n"
            f"使用 {self.usage} 查看具体命令的详细用法"
        )

    def execute(self, args: List[str]) -> Any:
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


class CommandManager:
    """
    命令管理器。

    提供命令的注册、查找和执行能力。
    """

    def __init__(self) -> None:
        """
        初始化命令管理器，创建空的命令注册表和线程锁，并注册默认帮助命令。
        """
        self._commands: Dict[str, Command] = {}
        # 缩写 -> 命令名称的映射
        self._abbr_map: Dict[str, str] = {}
        # 可重入锁，允许同一线程多次获取
        self._lock = threading.RLock()
        # 帮助命令实例（独立于 _commands，不通过 register 注册）
        self._help_command: Optional[HelpCommand] = HelpCommand(self)

    def register(self, command: Command) -> None:
        """
        注册命令。

        Args:
            command: 要注册的命令实例。

        Raises:
            ValueError: 当缩写已被其他命令使用时抛出。
            ValueError: 当尝试注册帮助命令或名称与帮助命令冲突时抛出。
        """
        with self._lock:
            # 不允许注册帮助命令及其子类
            if isinstance(command, HelpCommand):
                raise ValueError("不允许通过 register 注册帮助命令，请使用 set_help_command")  # noqa: TRY004
            # 命令名称和命令缩写
            name = command.name.lower()
            abbr = command.abbr.lower() if command.abbr else None
            # 名称不能与帮助命令的 name 一致
            if self._help_command and name == self._help_command.name.lower():
                raise ValueError(f"命令名称 '{name}' 与帮助命令冲突，请使用其他名称")
            if abbr:
                # 检查是否与帮助命令缩写冲突
                if self._help_command and self._help_command.abbr and abbr == self._help_command.abbr.lower():
                    raise ValueError(f"缩写 '{abbr}' 与帮助命令冲突，请使用其他缩写")
                # 检查缩写是否已被使用
                if abbr in self._abbr_map and self._abbr_map[abbr] != name:
                    raise ValueError(f"缩写 '{abbr}' 已被命令 '{self._abbr_map[abbr]}' 使用")
                # 注册缩写映射
                self._abbr_map[abbr] = name
            # 注册命令
            self._commands[name] = command

    def unregister(self, name: str) -> None:
        """
        取消注册命令。

        Args:
            name: 要取消注册的命令名称。

        Raises:
            ValueError: 当尝试取消注册帮助命令时抛出。
        """
        with self._lock:
            # 转换为小写
            name_lower = name.lower()
            # 不允许取消注册帮助命令
            if self._help_command and name_lower == self._help_command.name.lower():
                raise ValueError("不允许取消注册帮助命令")
            # 检查命令是否存在
            if name_lower in self._commands:
                # 移除该命令的缩写映射
                command = self._commands[name_lower]
                # 检查命令是否有缩写
                if command.abbr:
                    abbr_lower = command.abbr.lower()
                    if abbr_lower in self._abbr_map:
                        del self._abbr_map[abbr_lower]
                # 移除命令实例
                del self._commands[name_lower]

    def get_help_command(self) -> Optional['HelpCommand']:
        """
        获取帮助命令实例。

        Returns:
            帮助命令实例，如果未设置则返回 None。
        """
        with self._lock:
            # 返回帮助命令实例
            return self._help_command

    def set_help_command(self, command: HelpCommand) -> None:
        """
        设置帮助命令实例，直接覆盖旧的帮助命令。

        Args:
            command: 帮助命令实例，不能为空，且 name 必须有值。

        Raises:
            ValueError: 当命令实例为空或 name 无值时抛出。
        """
        if not command:
            raise ValueError("帮助命令实例不能为空")
        if not command.name:
            raise ValueError("帮助命令的 name 必须有值")
        with self._lock:
            # 设置帮助命令实例
            self._help_command = command

    def clear(self) -> None:
        """
        清空所有已注册的命令。
        """
        with self._lock:
            self._commands.clear()
            self._abbr_map.clear()

    def get_command(self, name_or_abbr: str) -> Optional[Command]:
        """
        获取命令（支持命令名称或缩写，包括帮助命令）。

        Args:
            name_or_abbr: 命令名称或缩写（不区分大小写）。

        Returns:
            命令实例，未找到时返回 None。
        """
        with self._lock:
            # 转换为小写
            name_lower = name_or_abbr.lower()
            # 先按命令名称查找
            if name_lower in self._commands:
                return self._commands.get(name_lower)
            # 再按缩写查找，转换为命令名称
            resolved_name = self._abbr_map.get(name_lower)
            # 再次判断是否在命令中
            if resolved_name and resolved_name in self._commands:
                return self._commands.get(resolved_name)
            # 尝试匹配帮助命令
            if self._help_command:
                help_name = self._help_command.name.lower()
                help_abbr = self._help_command.abbr
                if name_lower == help_name or (help_abbr and name_lower == help_abbr.lower()):
                    return self._help_command
            return None

    def get_all_commands(self) -> Dict[str, Command]:
        """
        获取所有已注册的命令（包括帮助命令）。
        """
        with self._lock:
            # 复制命令字典，避免并发修改
            commands = self._commands.copy()
            # 包含帮助命令
            if self._help_command:
                commands[self._help_command.name.lower()] = self._help_command
            # 返回所有命令
            return commands

    def execute_command(self, command_name: str, args: List[str]) -> Any:
        """
        执行命令（支持命令名称或缩写）。

        Args:
            command_name: 命令名称或缩写。
            args: 命令参数列表。

        Returns:
            Any: 命令执行结果，可以是任意类型。

        Raises:
            CommandNotFoundError: 当命令未找到时抛出。
        """
        with self._lock:
            # 查找命令（支持名称或缩写，包括帮助命令）
            command = self.get_command(command_name)
            # 检查命令是否存在
            if not command:
                raise CommandNotFoundError(f"未知命令: {command_name}")
            # 执行命令
            return command.execute(args)

    def main_cli(
        self,
        on_startup: Optional[Callable[[List[str]], Any]] = None,
        on_shutdown: Optional[Callable[[List[str]], Any]] = None,
    ):
        """
        命令行入口方法，解析参数并执行对应命令。

        Args:
            on_startup: 启动回调，接收命令行参数列表；为 None 时跳过。
                        回调内部可自行打印输出，若需中断流程可直接抛出异常。
            on_shutdown: 关闭回调，接收命令行参数列表；为 None 时跳过。
                         注意：回调内部不能抛出异常。
        """
        # 解析命令行参数
        args = sys.argv[1:]

        try:
            # 执行启动回调（为空时跳过）
            if on_startup:
                on_startup(args)

            # 解析命令
            command_name = args[0].lower() if args else ""
            command_args = args[1:] if args else []
            # 无命令时显示帮助
            if not command_name:
                help_command = self.get_help_command()
                command_name = help_command.name if help_command else "help"

            # 执行命令
            try:
                self.execute_command(command_name, command_args)
            except CommandNotFoundError as e:
                help_command = self.get_help_command()
                help_name = help_command.name if help_command else "help"
                print(str(e))
                print(f"使用 'python -m {pkginfo.get_caller_top_package_name()} {help_name}' 查看可用命令")
                sys.exit(1)
        finally:
            # 执行关闭回调（为空时跳过）；注意：回调内部不能抛出异常
            if on_shutdown:
                on_shutdown(args)
