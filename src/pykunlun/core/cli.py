"""
命令基类和注册机制模块。

本模块提供命令系统的核心抽象类和注册机制，用于实现可扩展的命令行工具。
"""

import sys
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, MutableMapping
from typing import Any, Optional

from pykunlun.core.ctxt import Context, ContextHolder
from pykunlun.envinfo import pkginfo


class CliContext(Context):
    """
    命令行上下文基类。

    承载一次命令执行期间的跨切面状态。构造时须传入 ``raw_args``（无参时传空列表）；
    执行入口（如 :meth:`CommandManager.main_cli`）按需挂载解析回调、并读取各字段：

      - ``raw_args``：原始命令行参数（``sys.argv[1:]``），构造时传入、只读快照。
      - ``current_args``：当前（工作中）参数，初始为 ``raw_args`` 的副本；解析流程
        可对其变换（如剥离 ``--delim``、消费命令名）而不影响原始快照。
      - ``command_name``：命令名（由 ``current_args`` 首个 token 解析得到，小写）。
      - ``command_name_parse``：命令名解析回调。默认 :meth:`default_command_name_parse`
        取 ``current_args[0]`` 写回 ``command_name`` 并从 ``current_args`` 移除；可替换或置 None。
      - ``delim_str``：结果分隔符；设则命令在 stdout 结果前后各输出一行该串，便于
        外部程序在夹杂日志时精准截取（由调用方保证唯一）。
      - ``delim_str_parse``：分隔符解析回调。默认 :meth:`default_delim_str_parse` 从
        ``current_args`` 扫描 ``--delim`` 写回 ``delim_str``；可替换或置 None。
      - ``output_charset``：CLI 终端输出字符集；None 表示沿用运行环境默认。
      - ``output_charset_parse``：输出字符集解析回调。默认 :meth:`default_output_charset_parse`
        从 ``current_args`` 扫描 ``--output-charset``（未提供则 None，不动 stdout/stderr）写回
        ``output_charset``；可替换或置 None。
      - ``on_startup`` / ``on_shutdown``：生命周期回调 ``Callable[[CliContext], None]``，
        分别在 :meth:`init` 开头、:meth:`destroy` 中调用；默认 None。

    另含通用键值存储 ``_storage``（经 :meth:`get_storage` 暴露，类型为
    ``MutableMapping[str, Any]``，可塞普通 dict 或自定义后端如 Redis 版映射）供扩展挂载。

    方法：

      - :meth:`init`：执行前调用——依次运行 ``on_startup``、``delim_str_parse``、
        ``output_charset_parse``、应用 ``output_charset`` 到 stdout、``command_name_parse``。
      - :meth:`destroy`：执行后调用（运行 ``on_shutdown``）。
      - :meth:`print_delim`：按 ``delim_str`` 打印分隔符（为空则不输出）。
    """

    def __init__(self, raw_args: list[str]) -> None:
        self._storage: MutableMapping[str, Any] = {}
        self.raw_args: list[str] = raw_args
        self.current_args: list[str] = list(raw_args)
        self.command_name: str | None = None
        self.command_name_parse: Callable[[CliContext], None] | None = CliContext.default_command_name_parse
        self.delim_str: str | None = None
        self.delim_str_parse: Callable[[CliContext], None] | None = CliContext.default_delim_str_parse
        self.output_charset: str | None = None
        self.output_charset_parse: Callable[[CliContext], None] | None = CliContext.default_output_charset_parse
        self.on_startup: Callable[[CliContext], None] | None = None
        self.on_shutdown: Callable[[CliContext], None] | None = None

    def get_storage(self) -> MutableMapping[str, Any]:
        return self._storage

    def init(self) -> None:
        """
        初始化上下文（命令执行前调用）。

        依次：运行 ``on_startup``；运行 ``delim_str_parse`` / ``output_charset_parse``
        （仅当非 None）；把 ``output_charset`` 应用到 stdout；运行 ``command_name_parse``。
        """
        if self.on_startup is not None:
            self.on_startup(self)
        if self.delim_str_parse is not None:
            self.delim_str_parse(self)
        if self.output_charset_parse is not None:
            self.output_charset_parse(self)
        self._apply_output_charset()
        if self.command_name_parse is not None:
            self.command_name_parse(self)

    def destroy(self) -> None:
        """
        销毁上下文（命令执行后调用，无论成功失败）。

        运行 ``on_shutdown``（若有）。本方法不应抛出异常。
        """
        if self.on_shutdown is not None:
            self.on_shutdown(self)

    def print_delim(self) -> None:
        """
        向 stdout 打印一行结果分隔符。

        若 ``delim_str`` 为空（None 或空串）则不执行任何操作；否则输出 ``delim_str``。
        命令在结果输出前后各调用一次，即可用分隔符包裹结果，便于外部程序精准截取。
        """
        if self.delim_str:
            print(self.delim_str)

    def _apply_output_charset(self) -> None:
        """
        把 ``output_charset`` 应用到 stdout 与 stderr（就地 reconfigure）；为空则跳过。

        就地 reconfigure（而非替换整个对象）的好处：已引用 ``sys.stdout`` / ``sys.stderr``
        的 logging StreamHandler 等会自动随之生效，无需重绑 handler。
        """
        if self.output_charset:
            for stream in (sys.stdout, sys.stderr):
                try:
                    stream.reconfigure(encoding=self.output_charset, errors="replace")  # type: ignore[union-attr]
                except (AttributeError, ValueError):
                    pass

    @staticmethod
    def _extract_optional(args: list[str], flag: str) -> tuple[str | None, list[str]]:
        """
        从 args 中扫描可选标记，返回 ``(值或None, 剥离该标记后的 args)``。

        支持两种写法：``flag X``（两个 token）与 ``flag=X``（单个 token），可出现在任意
        位置；多次出现取最后一个非空值；空值或末尾裸 flag（缺值）均视为未设置。

        Args:
            args: 原始参数列表。
            flag: 标记名（如 ``"--delim"``）。

        Returns:
            二元组 ``(value, remaining)``：``value`` 为标记值或 None；
            ``remaining`` 为剥离该标记相关 token 后的参数列表（原顺序保留）。
        """
        value: str | None = None
        remaining: list[str] = []
        i = 0
        while i < len(args):
            token = args[i]
            if token == flag:
                # 形如 `flag X`：取下一个 token 作为值
                if i + 1 < len(args):
                    v = args[i + 1]
                    if v:
                        value = v
                    i += 2
                    continue
                i += 1
                continue
            if token.startswith(flag + "="):
                # 形如 `flag=X`：等号后即为值
                v = token[len(flag) + 1:]
                if v:
                    value = v
                i += 1
                continue
            remaining.append(token)
            i += 1
        return value, remaining

    @staticmethod
    def default_command_name_parse(ctx: "CliContext") -> None:
        """
        :attr:`command_name_parse` 的默认实现。

        取 ``ctx.current_args[0]``（小写）写回 ``ctx.command_name``，并从 ``current_args``
        移除该 token；``current_args`` 为空时 ``command_name`` 保持 None。
        """
        if ctx.current_args:
            ctx.command_name = ctx.current_args[0].lower()
            ctx.current_args = ctx.current_args[1:]

    @staticmethod
    def default_delim_str_parse(ctx: "CliContext") -> None:
        """
        :attr:`delim_str_parse` 的默认实现。

        从 ``ctx.current_args`` 扫描 ``--delim``（``--delim X`` / ``--delim=X``），将值
        写回 ``ctx.delim_str`` 并把该标记从 ``current_args`` 剥离；多次出现取最后一个
        非空。未提供则 ``delim_str`` 置 None。
        """
        delim, remaining = CliContext._extract_optional(ctx.current_args, "--delim")
        ctx.delim_str = delim
        ctx.current_args = remaining

    @staticmethod
    def default_output_charset_parse(ctx: "CliContext") -> None:
        """
        :attr:`output_charset_parse` 的默认实现。

        从 ``ctx.current_args`` 扫描 ``--output-charset``（``--output-charset X`` /
        ``--output-charset=X``），将值写回 ``ctx.output_charset`` 并剥离该标记；多次出现
        取最后一个非空。未显式提供则置 None（即不改动 stdout/stderr，沿用运行时默认；
        遇到乱码时由用户经 ``--output-charset`` 自行指定编码）。
        """
        charset, remaining = CliContext._extract_optional(ctx.current_args, "--output-charset")
        ctx.output_charset = charset
        ctx.current_args = remaining


# 当前 CliContext 槽位：基于 contextvars 的「当前命令行上下文」容器，
# 按线程/asyncio Task 隔离。执行入口用 context_holder.using(ctx) 设定作用域。
context_holder: ContextHolder[CliContext] = ContextHolder(
    f"_{pkginfo.get_own_top_package_name()}_cli_context"
)


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
    def abbr(self) -> str | None:
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
    def execute(self, ctx: CliContext) -> Any:
        """
        执行命令。

        Args:
            ctx: 当前命令行上下文。命令自身参数经 ``ctx.current_args`` 获取
                （已剥离 ``--delim`` 等全局标记、且命令名已被消费）；
                结果分隔符等跨切面信息也从 ctx 读取（如 ``ctx.print_delim()``）。

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


class CommandManager:
    """
    命令管理器。

    提供命令的注册、查找和执行能力。
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

        with context_holder.using(ctx):
            try:
                # 初始化：on_startup → 解析 --delim / --output-charset → 应用 charset → 解析命令名
                ctx.init()
                # 无命令时默认显示帮助
                if not ctx.command_name:
                    help_command = self.get_help_command()
                    ctx.command_name = help_command.name if help_command else "help"
                # 执行命令
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
