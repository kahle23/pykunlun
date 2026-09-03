"""
只读客户端代理（装饰器模式）。

:class:`RdbReadOnlyClient` 包裹任意 :class:`RdbClient`，在客户端层拦截写操作，
适用于 MySQL / PostgreSQL 等网络型数据库（驱动连接层无法指定只读）。
"""

import re
from collections.abc import Callable, Iterable
from types import ModuleType
from typing import Any

from .client import RdbClient


class RdbReadOnlyClient(RdbClient):
    """
    只读客户端代理（装饰器模式）：包裹任意 :class:`RdbClient`，在客户端层拦截写操作。

    适用场景：MySQL / PostgreSQL 等网络型数据库的驱动连接层无法指定只读
    （不同于 SQLite 的 URI ``mode=ro``），故由本代理在客户端层拦截写操作：
    :meth:`query` 执行前解析 SQL 首动词，命中写操作（INSERT/UPDATE/DELETE/CREATE/DROP/...）
    即抛出 :class:`ValueError`，读操作（SELECT/SHOW/WITH/EXPLAIN/...）照常委派给被包裹的真实客户端；
    :meth:`execute` 因定位为写、一律直接拒绝（不转发内层）。

    通常不由调用方直接构造——:class:`RdbManager` 在注册时若发现 :attr:`RdbCfg.read_only`
    为 True，会自动用本代理包裹真实客户端（见 :meth:`RdbManager._maybe_wrap_read_only`）；
    也可手动包裹：``RdbReadOnlyClient(SomeRdbClient(cfg))``。

    禁止动词集合：默认取 :attr:`DEFAULT_FORBIDDEN_VERBS`（常见写操作），
    构造时可通过 ``forbidden_verbs`` 传入自定义集合覆盖（用于收紧/放宽），
    运行时经 :meth:`get_forbidden_verbs` 读取。

    实现要点：

      - 本类是 :class:`RdbClient` 子类（IS-A），对外类型与接口与真实客户端一致，
        可无缝替换、可经 :meth:`RdbManager.register` / :meth:`RdbManager.get_client` 透明传递；
      - 构造时**不**调用 :meth:`RdbClient.__init__`（真实客户端已自完成校验与转换器初始化），
        仅持有内层客户端 ``_inner``，其余属性/方法经 :meth:`__getattr__` 透明转发；
      - :meth:`query` 为本类显式覆盖（拦截写动词 + 转发读操作）；:meth:`execute` 为本类显式覆盖
        且一律拒绝（execute 定位为写，只读库不应执行），不转发内层；
      - :meth:`get_driver` / :meth:`build_connect_kwargs` 为满足基类抽象方法而显式转发
        （抽象方法无法经 :meth:`__getattr__` 兜底，基类的抽象桩会遮蔽 ``__getattr__``）；
      - :meth:`get_connection` **未覆盖**——基类实现已组合 :meth:`get_driver` +
        :meth:`build_connect_kwargs`，二者均转发至内层，故沿用的基类模板方法即等效于
        内层 :meth:`~RdbClient.get_connection`；
      - :meth:`close` 显式转发——基类 ``close`` 为空操作且会遮蔽 :meth:`__getattr__`，
        必须显式转发才能释放内层资源（如连接池）。

    .. warning::
        经 :meth:`get_connection` 取得的裸连接**绕过本代理的写拦截**（写拦截仅在
        :meth:`query` / :meth:`execute` 调度链内生效：:meth:`query` 比对动词、
        :meth:`execute` 整体拒绝）。只读保障为启发式，确权请依赖数据库账号权限。

    :meth:`query` 的写拦截为**启发式**：仅比对 SQL 首个动词，无法覆盖 ``SELECT ... INTO``、CTE 写、
    多语句等伪装写操作（:meth:`execute` 则不分动词一律拒绝）；对 SQLite 这类已在连接层只读
    （``mode=ro``）的库属二次保险，无害。
    """

    # region ======== 类级常量与实例属性 ========

    #: 默认禁止的 SQL 首动词集合（DML 写 / DDL / DCL / 过程调用 / 数据加载）。
    #: 构造时未显式传入 ``forbidden_verbs`` 时采用此默认值。
    DEFAULT_FORBIDDEN_VERBS: frozenset[str] = frozenset({
        'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'MERGE',            # DML 写
        'CREATE', 'ALTER', 'DROP', 'TRUNCATE', 'RENAME',             # DDL
        'GRANT', 'REVOKE',                                           # DCL
        'CALL', 'EXEC', 'EXECUTE', 'LOAD',                           # 过程调用 / 数据加载
    })

    #: 预编译正则：剥离 SQL 前导空白与注释（``-- 行注释``、``/* 块注释 */``），捕获首个动词。
    #: 供 :meth:`_extract_first_sql_verb` 解析 SQL 首动词做只读拦截比对。
    _SQL_FIRST_VERB_RE = re.compile(r'^\s*(?:(?:--[^\n]*|/\*.*?\*/)\s*)*([A-Za-z]+)', re.DOTALL)

    #: 被包裹的真实客户端（类级类型标注，供静态检查；实例由 __init__ 写入）。
    _inner: RdbClient

    #: 当前实例生效的禁止动词集合（实例属性，构造时确定）。
    _forbidden_verbs: frozenset[str]

    # endregion

    # region ======== 构造与属性转发 ========

    def __init__(self, inner: RdbClient,
                 forbidden_verbs: Iterable[str] | None = None) -> None:
        """
        Args:
            inner: 被包裹的真实 :class:`RdbClient`（已完成配置校验与初始化）。
            forbidden_verbs: 自定义禁止的 SQL 首动词集合，覆盖 :attr:`DEFAULT_FORBIDDEN_VERBS`；
                元素会统一大写归一化。为 ``None`` 时沿用默认集合。

        .. note::
            不调 ``super().__init__()``：inner 已自完成校验/转换器初始化，代理只需持有引用。
            用 :func:`object.__setattr__` 直接落盘实例属性，绕过 :meth:`RdbClient.__setattr__`
            （其拦 ``db_type``），并确保 ``_inner`` 在任何 :meth:`__getattr__` 触发前就已就位，
            避免代理转发时的属性查找递归。
        """
        object.__setattr__(self, '_inner', inner)
        if forbidden_verbs is None:
            verbs = self.DEFAULT_FORBIDDEN_VERBS
        else:
            verbs = frozenset(v.upper() for v in forbidden_verbs)
        object.__setattr__(self, '_forbidden_verbs', verbs)

    def __getattr__(self, name: str) -> Any:
        """
        未在本类显式定义的属性，一律转发给内层客户端。

        覆盖 Python 默认属性查找失败回退：本代理显式实现了 :meth:`query` / :meth:`execute` /
        :meth:`close` 及 :attr:`db_type` / :meth:`get_driver` / :meth:`build_connect_kwargs`
        透传方法；其余（含 :attr:`cfg`、值转换器管理方法、:meth:`is_connection_open`、
        :meth:`get_connection` 等）经此方法透明委派给 ``_inner``，保证行为与真实客户端一致。
        其中 :attr:`cfg` 的静态类型沿用基类 :class:`RdbClient` 的实例属性标注（``RdbCfg``）。

        注意：``__getattr__`` 仅在常规查找失败时触发，故不会遮蔽本类显式定义的方法；
        但基类已有的具体方法（如 :meth:`get_connection`、:meth:`is_connection_open`）
        会先于 ``__getattr____`` 命中——这些方法基于 :meth:`get_driver` / :meth:`build_connect_kwargs`
        等已转发的钩子工作，行为与内层一致。
        """
        return getattr(self._inner, name)

    # endregion

    # region ======== SQL 动词解析与写拦截（内部工具） ========

    @classmethod
    def _extract_first_sql_verb(cls, sql: str) -> str:
        """
        提取 SQL 语句首个动词（大写归一化），供 :meth:`_reject_write_sql` 比对。

        跳过前导空白与 SQL 注释（``-- 行注释``、``/* 块注释 */``），
        取首个连续字母序列作为动词；无法识别（空串、首字符非字母等）时返回空串。
        """
        if not sql:
            return ''
        m = cls._SQL_FIRST_VERB_RE.match(sql)
        return m.group(1).upper() if m else ''

    def _reject_write_sql(self, sql: str) -> None:
        """
        若 SQL 首动词属于写操作，抛出 :class:`ValueError`；否则放行（不返回值）。

        Args:
            sql: 待校验的 SQL 语句。

        Raises:
            ValueError: SQL 首动词命中 :meth:`get_forbidden_verbs` 返回的集合时抛出。
        """
        verb = self._extract_first_sql_verb(sql)
        if verb in self._forbidden_verbs:
            raise ValueError(
                f"当前为只读客户端（read_only=True），禁止执行写操作："
                f"{verb}（SQL: {sql.strip()[:80]!r}）"
            )

    # endregion

    # region ======== 透传：核心标识/配置 ========

    @property
    def db_type(self) -> str:
        """透传内层客户端的数据库类型标识（满足基类抽象 property）。"""
        return self._inner.db_type

    def get_driver(self) -> ModuleType:
        """透传内层客户端的驱动模块（满足基类抽象方法）。"""
        return self._inner.get_driver()

    def build_connect_kwargs(self) -> dict[str, Any]:
        """透传内层客户端的连接参数（满足基类抽象方法）。"""
        return self._inner.build_connect_kwargs()

    # endregion

    # region ======== 拦截 + 转发：执行接口 ========

    def get_forbidden_verbs(self) -> frozenset[str]:
        """
        获取当前实例生效的禁止 SQL 首动词集合。

        返回的 :class:`frozenset` 不可变，可安全直接使用；如需自定义，
        请在构造时通过 ``forbidden_verbs`` 参数传入。

        Returns:
            禁止的 SQL 首动词集合（大写）。
        """
        return self._forbidden_verbs

    def query(self, sql: str, params: tuple[Any, ...] | None = None,
              converters: dict[type, Callable[[Any], Any]] | None = None) -> list[dict[str, Any]]:
        """
        只读查询（拦截写操作后转发给内层客户端）。

        先经 :meth:`_reject_write_sql` 拦截 INSERT/UPDATE/DELETE/... 等写操作，
        再将合法的读操作（SELECT 等）原样转发给内层 :meth:`~RdbClient.query`，
        参数语义与 :meth:`RdbClient.query` 完全一致。
        """
        self._reject_write_sql(sql)
        return self._inner.query(sql, params, converters)

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        """
        只读客户端禁止 :meth:`execute`。

        :meth:`execute` 定位为写操作（INSERT/UPDATE/DELETE/...），返回受影响行数；
        只读客户端不应执行任何写操作。需要查询请使用 :meth:`query`（支持 SELECT 等读操作，
        并对写动词做拦截）。本方法一律拒绝，不做 SQL 首动词判断——execute 的语义本身即写，
        即便传入 SELECT 也会被拒（SELECT 应走 :meth:`query`）。

        Raises:
            ValueError: 始终抛出。
        """
        raise ValueError(
            f"当前为只读客户端（read_only=True），禁止 execute 写操作；"
            f"如需查询请使用 query()。（SQL: {sql.strip()[:80]!r}）"
        )

    def close(self) -> None:
        """转发至内层 :meth:`~RdbClient.close`，释放底层资源（如连接池）。"""
        self._inner.close()

    # endregion
