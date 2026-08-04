"""
命令管理器模块。

提供 :class:`CommandManager`：命令的注册、查找、执行能力，以及命令行入口
:meth:`CommandManager.main_cli`。
"""

import sys
import threading
from collections.abc import Callable
from typing import Any, Optional

from pykunlun.cli.command import Command, CommandNotFoundError, HelpCommand
from pykunlun.cli.context import CliContext, cli_context_holder
from pykunlun.envinfo import pkginfo


class CommandManager:
    """
    命令管理器。

    提供命令的注册、查找和执行能力。线程安全（内部用可重入锁保护）。
    """

    def __init__(self) -> None:
        """
        初始化命令管理器，创建空的命令注册表和线程锁，并注册默认帮助命令。
        """
        self._commands: dict[str, Command] = {}
        # 缩写 -> 命令名称的映射
        self._abbr_map: dict[str, str] = {}
        # 可重入锁，允许同一线程多次获取
        self._lock = threading.RLock()
        # 帮助命令实例（独立于 _commands，不通过 register 注册）
        self._help_command: HelpCommand | None = HelpCommand(self)

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

    def get_command(self, name_or_abbr: str) -> Command | None:
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

    def get_all_commands(self) -> dict[str, Command]:
        """
        获取所有已注册的命令（包括帮助命令）。

        Returns:
            命令字典（键为命令名称小写，值为命令实例）；含帮助命令。
        """
        with self._lock:
            # 复制命令字典，避免并发修改
            commands = self._commands.copy()
            # 包含帮助命令
            if self._help_command:
                commands[self._help_command.name.lower()] = self._help_command
            # 返回所有命令
            return commands

    def _execute_command(self, ctx: CliContext) -> Any:
        """
        执行命令（私有；按 ``ctx.command_name`` 查找并执行）。

        Args:
            ctx: 当前命令行上下文（提供 command_name、current_args 等）。

        Returns:
            Any: 命令执行结果，可以是任意类型。

        Raises:
            CommandNotFoundError: 当命令未找到时抛出。
        """
        with self._lock:
            # 按 command_name 查找命令（支持名称或缩写，包括帮助命令）
            command = self.get_command(ctx.command_name) if ctx.command_name else None
            # 检查命令是否存在
            if not command:
                raise CommandNotFoundError(f"未知命令: {ctx.command_name}")
            # 执行命令
            return command.execute(ctx)

    def main_cli(
        self,
        on_startup: Callable[[CliContext], None] | None = None,
        on_shutdown: Callable[[CliContext], None] | None = None,
    ) -> Any:
        """
        命令行入口方法：读 ``sys.argv`` 构建 :class:`CliContext`、初始化、分发命令、销毁。

        ``--delim`` / ``--output-charset`` 等全局标记由 ``ctx.init()`` 统一解析（可出现在
        任意位置，含命令名之前）；命令名由 ``current_args[0]`` 解析。无命令时默认显示帮助。

        Args:
            on_startup: 启动回调，接收 CliContext；为 None 跳过。在 ``ctx.init()`` 中首先调用。
            on_shutdown: 关闭回调，接收 CliContext；为 None 跳过。在 ``ctx.destroy()`` 中调用。
        """
        # 读 sys.argv 构建上下文，并把生命周期回调挂到 ctx 上
        ctx = CliContext(sys.argv[1:])
        ctx.on_startup = on_startup
        ctx.on_shutdown = on_shutdown

        with cli_context_holder.using(ctx):
            try:
                # 初始化：on_startup → 解析 --delim / --output-charset → 应用 charset → 解析命令名
                ctx.init()
                # 无命令时默认显示帮助
                if not ctx.command_name:
                    help_command = self.get_help_command()
                    ctx.command_name = help_command.name if help_command else "help"
                # 执行命令（捕获 CommandNotFoundError 以打印帮助提示）
                try:
                    self._execute_command(ctx)
                except CommandNotFoundError as e:
                    help_command = self.get_help_command()
                    help_name = help_command.name if help_command else "help"
                    print(str(e))
                    print(f"使用 'python -m {pkginfo.get_caller_top_package_name()} {help_name}' 查看可用命令")
                    sys.exit(1)
            finally:
                # 销毁：on_shutdown 等；destroy 不应抛异常，但兜底保护
                try:
                    ctx.destroy()
                except Exception:
                    pass
