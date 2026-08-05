"""
命令行上下文模块。

提供 :class:`CliContext` 与对应的「当前上下文」槽位 :data:`cli_context_holder`，
承载一次命令执行期间的跨切面状态。
"""

import sys
from collections.abc import Callable, MutableMapping
from typing import Any

from pykunlun.core.context import Context, ContextHolder
from pykunlun.envinfo import pkginfo


class CliContext(Context):
    """
    命令行上下文。

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


# 当前命令行上下文槽位：基于 contextvars 的容器，按线程/asyncio Task 隔离。
# 执行入口用 cli_context_holder.using(ctx) 设定作用域。
cli_context_holder: ContextHolder[CliContext] = ContextHolder(
    f"_{pkginfo.get_own_top_package_name()}_cli_context"
)
