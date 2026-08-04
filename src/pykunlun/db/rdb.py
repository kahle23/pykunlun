"""
关系型数据库操作的底层抽象模块，定义驱动策略接口与注册表。

采用策略模式：

  - :class:`RdbClient` 为抽象基类，绑定一份 :class:`RdbCfg` 配置，定义跨数据库类型的统一接口（含 SQL 执行）；
  - 各数据库具体实现（如 MySQL、PostgreSQL）由上层包提供；其中基于标准库的实现（如 SQLite）可直接置于本包内；
  - 通过 :class:`RdbManager` 维护 ``db_type -> 客户端类`` 注册表，按 :meth:`RdbCfg.db_type` 工厂化创建绑定实例。

本模块提供抽象与注册表，并为无第三方依赖的部分（SQLite）提供内置实现。
依赖第三方驱动的实现（如 MySQL 的 pymysql、PostgreSQL 的 psycopg2）
由上层包在导入时通过 :meth:`RdbManager.register_client_class` 注册实现类。
随后即可用 :meth:`RdbManager.register` 传入 :class:`RdbCfg` 工厂化创建实例。

仅依赖 Python 标准库与 pykunlun 自身工具模块。
"""

import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from pykunlun.util import logutil, validation

log = logutil.getLogger(__name__)


# region ======== 连接配置 ========

@dataclass
class RdbCfg:
    """
    SQL 数据库连接配置。

    封装数据库连接所需的所有参数，支持多种数据库类型。
    所有字段均可选（默认 ``None`` 表示未设置），
    必填性与默认值交由各 :class:`RdbClient` 实现的 :meth:`~RdbClient._validate_and_prepare_cfg`
    在构造客户端时校验与补全，
    便于仅需少量参数的数据库（如 SQLite，仅需 :attr:`database`）使用。

    Attributes:
        host: 服务器地址。
        port: 服务端口。
        username: 用户名。
        password: 密码。
        database: 数据库名称；对文件型数据库（如 SQLite）为数据库文件路径。
        db_type: 数据库类型标识，如 ``mysql``、``postgresql``、``sqlite``；
            省略（``None``）时由所构造的实现类 :attr:`~RdbClient.db_type` 推导，显式传入则校验一致性。
        charset: 数据库字符集；
            省略时由实现类按数据库类型补全默认（如 MySQL ``utf8mb4``、PostgreSQL ``utf8``），无默认则用驱动默认。
        validation_query: 连接探活 SQL；省略时补全为 ``SELECT 1``。
        read_only: 是否以只读方式打开连接（默认 ``False``）。
            适用于查询外部正在写入的库（如其他进程的 SQLite 文件库），避免误写、避免锁库。
            各驱动按自身方式支持：
              - SQLite：用 URI ``file:<path>?mode=ro`` 打开（连不存在的库会失败而非创建），
                由数据库引擎自身拒绝任何写操作；``:memory:`` 内存库忽略此选项（内存库不支持只读）。
              - MySQL / PostgreSQL 等网络库：驱动连接层无法指定只读，由 :class:`RdbManager` 在注册时
                自动用 :class:`ReadOnlyClient` 代理包裹真实客户端，于客户端层解析 SQL 首动词，
                命中写操作（INSERT/UPDATE/DELETE/CREATE/DROP/...）即拒绝；为启发式拦截，非数据库级权限保障。

    校验策略：RdbCfg 为纯数据容器，自身不做任何校验；
    各数据库的校验与默认值补全（必填字段差异）
    交由 :meth:`RdbClient._validate_and_prepare_cfg` 在构造客户端实例时自动完成。
    """

    db_type: str | None = None
    charset: str | None = None
    host: str | None = None
    port: int | None = None
    username: str | None = None
    password: str | None = None
    database: str | None = None
    validation_query: str | None = None
    read_only: bool = False


# endregion


# region ======== 驱动策略抽象基类 ========

class RdbClient(ABC):
    """
    关系型数据库驱动策略抽象基类（绑定一份 :class:`RdbCfg` 配置）。

    每个实例绑定一个 :class:`RdbCfg`，把"因数据库而异"的差异收敛为可覆盖的钩子方法，
    把"放之四海皆准"的执行逻辑统一写在本类。
    新增一种数据库 = 继承本类并覆盖少数钩子，基类的执行逻辑无需改动。

    方法分两层：

    【配置层】—— 构造时即校验
      - ``db_type``             : 数据库类型标识（实现类硬编码的类级常量）；
                                  cfg.db_type 省略时由本类推导，显式传入则校验一致性。
      - ``_validate_and_prepare_cfg``: 校验 cfg 必填字段并补全可推导默认值；
                                  默认按网络库校验 host/username/password/database/port
                                  及端口范围、补全 validation_query；port/charset 等
                                  数据库特定默认由子类覆盖时填充（先填再调 super）。
                                  文件型数据库（如 SQLite）可整体覆盖为只校验 database。
                                  由 ``__init__`` 自动调用，**构造即校验并补全**。

    【驱动差异钩子】—— 子类按需实现/覆盖
      - ``get_driver``          : 返回驱动模块对象（pymysql/psycopg2/sqlite3...）。
      - ``build_connect_kwargs``: 返回 ``connect()`` 的关键字参数
                                  （charset vs client_encoding vs database）。
      - ``is_connection_open``  : 判断连接是否可用（pymysql 的 ``.open`` /
                                  psycopg2 的 ``.closed``），默认鸭子类型。
      - ``_normalize_rows``     : 把游标结果统一整形成 ``list[dict]``，
                                  兼容字典游标与元组游标。

    【通用执行接口】—— 基类实现，组合上述钩子，调用方直接使用
      - ``get_connection``      : ``get_driver().connect(**build_connect_kwargs())``
      - ``query``               : ``get_connection → execute → fetchall → _normalize_rows``
      - ``execute``             : ``get_connection → execute → commit/rollback``
      - ``close``               : 释放底层连接资源（默认空操作，连接池子类覆盖）

    调用链示意::

        client.query(sql)
          └─ get_connection()
               ├─ get_driver()                              # 钩子
               └─ driver.connect(**build_connect_kwargs())  # 钩子
          └─ cursor.execute / fetchall                      # DB-API 2.0，各驱动一致
          └─ _normalize_rows(...)                           # 钩子

    其中 :meth:`get_driver`、:meth:`build_connect_kwargs` 为必须实现的抽象方法，
    其余方法提供跨驱动通用的默认实现，可按需覆盖。

    通常直接构造使用（构造时自动细校验），也可注册到 :class:`RdbManager` 按名称管理::

        client = SomeRdbClient(cfg)
        client.execute("INSERT INTO t VALUES (?, ?)", (1, 'a'))
        rows = client.query("SELECT * FROM t")
    """

    # region ======== 构造与配置校验 ========

    def __init__(self, cfg: RdbCfg) -> None:
        """
        Args:
            cfg: 绑定的数据库配置对象。

        Raises:
            ValueError: 显式声明的 ``db_type`` 与本实现类 :attr:`db_type` 不一致、
                或 :meth:`_validate_and_prepare_cfg` 校验不通过时抛出。
        """
        self.cfg = cfg
        # db_type：cfg 未声明（None）时由本实现类的 db_type 推导；显式声明则校验
        # 一致性，不符即说明配置用错了实现类。
        if cfg.db_type is None:
            cfg.db_type = self.db_type
        elif cfg.db_type != self.db_type:
            raise ValueError(
                f"数据库类型不匹配：配置 db_type={cfg.db_type!r}，"
                f"实现类 {type(self).__name__} 仅支持 {self.db_type!r}"
            )
        # 构造即校验+补全：不同数据库的必填字段与默认值不同，交由各实现判定
        self._validate_and_prepare_cfg()

    def __setattr__(self, name: str, value: Any) -> None:
        """
        拦截实例属性赋值，保护 :attr:`db_type` 与 :attr:`cfg` 不被运行时篡改。

        - ``db_type``：基类虽把它声明为抽象只读 property，但子类为满足抽象约束会用类级常量
          ``db_type = 'mysql'`` 覆盖——该常量是普通字符串（非 data descriptor），会遮蔽基类 property，
          使 property 的只读保护失效，``instance.db_type = x`` 将悄悄创建实例级遮蔽。
          本方法显式抛 :class:`AttributeError` 堵住此缺口。
        - ``cfg``：允许构造时首次赋值（由 :meth:`__init__` 触发），构造完成后禁止替换。
          绑定的 cfg 已经过 :meth:`_validate_and_prepare_cfg` 校验与默认值补全，
          运行期整体替换会绕过校验、破坏不变量；如需变更配置请重新构造实例。
          cfg 内部字段的逐个修改同样会绕过校验，应避免。
        其余属性（连接池参数等）照常赋值。

        Raises:
            AttributeError: 尝试给实例的 ``db_type`` 赋值，或构造完成后再次给 ``cfg`` 赋值时抛出。
        """
        if name == 'db_type':
            raise AttributeError(
                f"{type(self).__name__}.db_type 是实现类硬编码的类级常量，"
                f"代表本类所属的数据库类型，禁止运行时修改。"
            )
        if name == 'cfg' and 'cfg' in self.__dict__:
            raise AttributeError(
                f"{type(self).__name__}.cfg 在构造完成后不可替换（绑定配置已经校验），"
                f"如需变更配置请重新构造实例。"
            )
        super().__setattr__(name, value)

    def _validate_and_prepare_cfg(self) -> None:
        """
        校验并补全绑定的 :attr:`cfg`：必填字段缺失报错，可推导字段填默认。

        不同数据库所需字段与默认值不同（如文件型数据库仅需 database，端口/字符集各数据库标准不同），
        故本方法为**实例方法**，由各实现按自身规则覆盖。
        本默认实现按网络型数据库处理：

          - 必填校验 ``host`` / ``username`` / ``password`` / ``database``（缺失报错），
            并校验 ``port`` 在 1-65535 范围内；
          - ``validation_query`` 缺失时补 ``SELECT 1``。

        ``port`` / ``charset`` 等可推导默认值由各子类在覆盖时**先填再调** ``super()``
        （如 MySQL 填 3306/utf8mb4、PostgreSQL 填 5432/utf8）；
        文件型数据库（如 SQLite）可整体覆盖为只校验 ``database``。

        本方法由 :meth:`__init__` 自动调用，确保构造出的实例配置一定有效且完整。

        Raises:
            ValueError: 必填字段为空、或端口越界时抛出。
        """
        cfg = self.cfg
        # 必填字段（网络型核心，无法推导）
        validation.check_required_fields_not_empty(
            cfg, ['host', 'username', 'password', 'database'], '数据库配置')
        # port：必填并校验范围（默认值由子类覆盖时先填）
        port = cfg.port
        if port is None:
            raise ValueError("数据库配置缺少必填字段: port")
        if not (1 <= port <= 65535):
            raise ValueError(f"端口号必须在 1-65535 范围内，当前值: {port}")
        # validation_query：缺失填通用默认
        if not cfg.validation_query:
            cfg.validation_query = 'SELECT 1'

    def _normalize_rows(self, cursor, rows: list,
                        converters: dict[type, Callable[[Any], Any]] | None = None) -> list[dict]:
        """
        将查询结果行整形为字典列表。

        兼容两类游标：
          - 字典游标（如 pymysql DictCursor）：行本身即为 dict，直接按键取值；
          - 元组游标（如 psycopg2、sqlite3）：通过 ``cursor.description`` 取列名与行 zip 成字典；
            无法获取列名时回退为 ``field_0``、``field_1``...。

        Args:
            cursor: 已执行查询的游标，用于获取 ``description``。
            rows: ``cursor.fetchall()`` 返回的行列表。
            converters: 值转换器映射，``{原始类型: 转换函数}``。
                整形时对每个值先按其**精确类型**（:func:`type`）查找转换函数，未命中则沿其
                MRO（方法解析顺序）回退查父类，兼容驱动返回的子类型实例；仍无命中则保持原样。
                ``None`` 值不参与转换。``None`` 或空映射表示不转换。

        Returns:
            字典列表，每个字典表示一行，键为列名。
        """
        # 空结果直接返回空列表
        if not rows:
            return []

        # 值转换器：按类型匹配转换函数，保持原样
        def _convert(d: dict) -> dict:
            if not converters:
                return d
            result: dict = {}
            for k, v in d.items():
                # None 值不参与转换（保持原样）
                if v is None:
                    result[k] = v
                    continue
                # 精确类型优先；未命中则沿 MRO 回退查父类（兼容驱动返回的子类型实例）
                fn = converters.get(type(v))
                if fn is None:
                    for base in type(v).__mro__[1:]:
                        fn = converters.get(base)
                        if fn is not None:
                            break
                result[k] = fn(v) if fn is not None else v
            return result

        # 字典游标：行本身即为 dict
        if isinstance(rows[0], dict):
            return [_convert(dict(row)) for row in rows]

        # 元组游标：借助 description 取列名
        cols = [desc[0] for desc in cursor.description] if cursor.description else None
        if cols is None:
            log.warning("无法获取列名描述，将使用位置索引 field_0, field_1...")
        result = []
        for row in rows:
            d = dict(zip(cols, row)) if cols else {f'field_{i}': v for i, v in enumerate(row)}
            result.append(_convert(d))
        return result

    # endregion

    # region ======== 驱动钩子与执行接口 ========

    @property
    @abstractmethod
    def db_type(self) -> str:
        """
        本实现类代表的数据库类型标识（如 ``mysql``、``postgresql``、``sqlite``）。

        由各实现类以**类级常量**形式硬编码提供，标识"本类是哪种数据库的驱动"。
        基类声明为抽象只读 property，强制子类在类级覆盖；
        其运行时不可修改性由 :meth:`__setattr__` 显式拦截保证（详见该方法的说明）。
        """
        pass

    @abstractmethod
    def get_driver(self):
        """
        获取数据库驱动模块。

        各实现自报模块名与 pip 安装名，未安装时建议通过 :func:`pykunlun.modutil.import_module` 自动安装。

        Returns:
            数据库驱动模块对象。
        """
        pass

    @abstractmethod
    def build_connect_kwargs(self) -> dict[str, Any]:
        """
        构建驱动 ``connect()`` 所需的关键字参数（基于绑定的 :attr:`cfg`）。

        用于封装各数据库连接参数差异，例如：
          - MySQL 通过 ``charset`` 设置字符集；
          - PostgreSQL 通过 ``client_encoding`` 设置字符集；
          - SQLite 仅需 ``database``（文件路径）。

        Returns:
            连接参数字典。
        """
        pass

    def is_connection_open(self, connection) -> bool:
        """
        判断连接是否处于打开状态。

        默认按鸭子类型处理常见驱动：优先取 ``open`` 属性（如 pymysql），否则取 ``closed`` 属性取反（如 psycopg2）。
        驱动既无 ``open`` 也无 ``closed`` 属性时乐观返回 True，由实际执行时的错误驱动重连。

        Args:
            connection: 数据库连接对象。

        Returns:
            连接可用返回 True，否则 False。
        """
        if hasattr(connection, 'open'):
            return bool(connection.open)
        if hasattr(connection, 'closed'):
            return not bool(connection.closed)
        return True

    def get_connection(self):
        """
        打开并返回一个新的数据库连接。

        基于绑定的 :attr:`cfg`，经 :meth:`get_driver` 与 :meth:`build_connect_kwargs` 建立连接，
        **连接的生命周期由调用方负责**（使用后需关闭）。

        本方法每次调用都创建新连接，不维护连接池；需要连接复用的上层模块（如带连接池的客户端）可基于本方法或直接使用驱动自行管理。

        注意：对 SQLite 的 ``:memory:`` 内存库，每次调用得到的是彼此隔离的独立内存库，跨连接不可见数据；
        如需跨语句共享数据，请使用文件库，或由调用方持有同一个连接。

        Returns:
            数据库连接对象。
        """
        driver = self.get_driver()
        return driver.connect(**self.build_connect_kwargs())

    def query(self, sql: str, params: tuple[Any, ...] | None = None,
              converters: dict[type, Callable[[Any], Any]] | None = None) -> list[dict]:
        """
        执行查询并返回结果（自动管理连接生命周期）。

        **职责：只做查询（读操作）**——SELECT / SHOW / WITH / EXPLAIN 等，
        结果经 :meth:`_normalize_rows` 整形为字典列表；新增、修改、删除等写操作请用 :meth:`execute`。
        内部每次创建并关闭一个连接（connect-per-call）。

        Args:
            sql: SQL 查询语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。
            converters: 值转换器映射，``{原始类型: 转换函数}``，由调用方按需传入；
                ``None`` 表示不做任何转换，保持驱动返回的原始类型。
                转换先按值的**精确类型**（:func:`type`）匹配，未命中则沿其 MRO 回退查父类，
                兼容驱动返回的子类型；``None`` 值不参与转换。

        Returns:
            查询结果列表，每个元素是一个字典，键为列名。
        """
        connection = None
        cursor = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            # params 为 None 时不传第二参数：sqlite3 会将 None 当作待绑定参数报错，
            # pymysql/psycopg2 虽容忍 None，但统一守卫以兼容所有驱动。
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            rows = cursor.fetchall()
            return self._normalize_rows(cursor, rows, converters)
        except Exception:
            log.exception("数据库查询失败")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        """
        执行 SQL 语句（自动管理连接生命周期）。

        **职责：只做写操作**——INSERT（新增）/ UPDATE（修改）/ DELETE（删除）及 DDL
        （CREATE / DROP / ALTER 等），自动提交事务；查询（SELECT 等）请用 :meth:`query`。
        内部每次创建并关闭一个连接（connect-per-call），异常时回滚。

        与 :meth:`query` 保持一致的异常处理范式：连接获取置于 ``try`` 内、
        异常时落日志、``finally`` 用 ``if`` 守卫关闭资源；
        回滚失败不会掩盖原始异常（仅打 warning 后重抛原异常）。

        Args:
            sql: SQL 语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。

        Returns:
            受影响的行数。
        """
        connection = None
        cursor = None
        try:
            connection = self.get_connection()
            cursor = connection.cursor()
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            connection.commit()
            return int(cursor.rowcount)
        except Exception:
            log.exception("数据库执行失败")
            # 回滚失败不应掩盖原始异常：吞掉二次异常仅打 warning，原始异常照常重抛
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    log.warning("事务回滚失败", exc_info=True)
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def close(self) -> None:
        """
        释放底层连接资源。

        默认实现为空操作（connect-per-call 的客户端每次开关连接，无需集中释放）。
        持有连接池等长期资源的客户端子类应覆盖本方法，在关闭时归还或释放资源。
        """
        pass

    # endregion


# endregion


# region ======== 只读客户端代理（装饰器） ========

class ReadOnlyClient(RdbClient):
    """
    只读客户端代理（装饰器模式）：包裹任意 :class:`RdbClient`，在客户端层拦截写操作。

    适用场景：MySQL / PostgreSQL 等网络型数据库的驱动连接层无法指定只读
    （不同于 SQLite 的 URI ``mode=ro``），故由本代理在客户端层拦截写操作：
    :meth:`query` 执行前解析 SQL 首动词，命中写操作（INSERT/UPDATE/DELETE/CREATE/DROP/...）
    即抛出 :class:`ValueError`，读操作（SELECT/SHOW/WITH/EXPLAIN/...）照常委派给被包裹的真实客户端；
    :meth:`execute` 因定位为写、一律直接拒绝（不转发内层）。

    通常不由调用方直接构造——:class:`RdbManager` 在注册时若发现 :attr:`RdbCfg.read_only`
    为 True，会自动用本代理包裹真实客户端（见 :meth:`RdbManager._maybe_wrap_read_only`）；
    也可手动包裹：``ReadOnlyClient(SomeRdbClient(cfg))``。

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
    DEFAULT_FORBIDDEN_VERBS: frozenset = frozenset({
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
    _forbidden_verbs: frozenset

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

    def get_driver(self):
        """透传内层客户端的驱动模块（满足基类抽象方法）。"""
        return self._inner.get_driver()

    def build_connect_kwargs(self) -> dict[str, Any]:
        """透传内层客户端的连接参数（满足基类抽象方法）。"""
        return self._inner.build_connect_kwargs()

    # endregion

    # region ======== 拦截 + 转发：执行接口 ========

    def get_forbidden_verbs(self) -> frozenset:
        """
        获取当前实例生效的禁止 SQL 首动词集合。

        返回的 :class:`frozenset` 不可变，可安全直接使用；如需自定义，
        请在构造时通过 ``forbidden_verbs`` 参数传入。

        Returns:
            禁止的 SQL 首动词集合（大写）。
        """
        return self._forbidden_verbs

    def query(self, sql: str, params: tuple[Any, ...] | None = None,
              converters: dict[type, Callable[[Any], Any]] | None = None) -> list[dict]:
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


# endregion


# region ======== 驱动客户端管理器（注册表） ========

class RdbManager:
    """
    关系型数据库驱动客户端管理器（双层注册表：类 + 实例）。

    维护两张注册表：

      - **类注册表** ``db_type -> RdbClient 子类(class)``：管理各数据库类型对应的实现类。
        注册键取自类自身的 :attr:`~RdbClient.db_type`（自动小写归一化），无需调用方显式提供。
        通过 :meth:`register_client_class` 注册后，即可用 :meth:`register` 直接传入 :class:`RdbCfg`，
        由本管理器按 ``cfg.db_type`` 工厂化创建实例——调用方无需手动 ``new``。
      - **实例注册表** ``name -> RdbClient 实例``：管理绑定具体配置的客户端实例，每个实例绑定一份 :class:`RdbCfg`。
        同一管理器可注册多份不同配置的实例，通过名称（别名）区分。

    关于 ``name`` 的用途：name 是注册实例的**别名**，用于区分同一数据库类型的不同连接配置，而非区分数据库类型本身。
        典型场景是按环境隔离——例如为开发环境与测试环境各注册一个 :class:`MysqlRdbClient` 实例
        （实现类相同、连接配置不同），通过 ``name="dev"`` / ``name="test"`` 分别访问；
    也可按业务模块命名（如 ``"order_db"``、``"user_db"``）。

    :attr:`DEFAULT_NAME` 为默认实例名称。
    除 :meth:`register_client_class`（按类自身 db_type 归档）与 :meth:`register`（须显式提供 name）外，
    其余方法（:meth:`unregister`、:meth:`get_client`、:meth:`get_connection`、:meth:`query`、:meth:`execute`）
    的 ``name`` 参数均可省略，省略时使用默认名称。

    本类额外提供 :meth:`get_connection`、:meth:`query`、:meth:`execute` 便捷方法，
    比直接调用 :class:`RdbClient` 同名方法多一个 ``name`` 参数（用于选择已注册的实例），其余参数语义一致。

    用法示例::

        manager = RdbManager()

        # 1) 注册实现类（db_type 取自类自身，一次性）
        manager.register_client_class(SqliteClient)

        # 2) 注册实例：直接传 RdbCfg，按 cfg.db_type 自动 new
        cfg = RdbCfg(db_type='sqlite', database='/tmp/test.db')
        manager.register("default", cfg)

        # 也仍可显式传入已构造的实例（不依赖类注册表）
        # manager.register("default", SqliteClient(cfg))

        # 3) 通过管理器直接执行（name 可省略，默认 "default"）
        manager.execute("INSERT INTO t VALUES (1)")
        rows = manager.query("SELECT * FROM t")

        # 指定 name 操作非默认实例
        manager.execute("...", name="other")
    """

    # region ======== 构造 ========

    #: 默认实例名称
    DEFAULT_NAME = "default"

    def __init__(self, config_loader: Callable[['RdbManager', str], None] | None = None) -> None:
        """
        Args:
            config_loader: 配置加载器，当 :meth:`get_client` 按名称查找失败时调用。
                签名 ``(manager: RdbManager, name: str) -> None``，
                由 loader 自行决定加载策略（如一次性加载、按需加载等）。
                为 ``None`` 时不启用 fallback。
        """
        # 类注册表：db_type -> RdbClient 子类（用于按 cfg.db_type 工厂化创建实例）
        self._class_registry: dict[str, type[RdbClient]] = {}
        # 实例注册表：name -> RdbClient 实例（本实例独有）
        self._client_registry: dict[str, RdbClient] = {}
        self._lock = threading.RLock()
        self._config_loader = config_loader

    # endregion

    # region ======== getter ========

    def get_config_loader(self) -> Callable[['RdbManager', str], None] | None:
        """
        获取配置加载器。

        Returns:
            配置加载器 callable，未设置时返回 None。
        """
        return self._config_loader

    # endregion

    # region ======== 类注册表（db_type -> RdbClient 子类） ========

    def _maybe_wrap_read_only(self, client: RdbClient) -> RdbClient:
        """
        若客户端配置为只读（``cfg.read_only=True``）且尚未被只读代理包裹，则套一层
        :class:`ReadOnlyClient`；否则原样返回。

        使本管理器注册的只读实例统一具备客户端层写拦截能力，无论实例是按配置工厂化创建、
        还是由调用方预先构造后传入 :meth:`register`。已是 :class:`ReadOnlyClient` 的不再重复包裹。

        Args:
            client: 待处理的客户端实例。

        Returns:
            原客户端，或其只读代理。
        """
        if client.cfg.read_only and not isinstance(client, ReadOnlyClient):
            return ReadOnlyClient(client)
        return client

    def _create_client_from_cfg(self, cfg: RdbCfg) -> RdbClient:
        """
        按 ``cfg.db_type`` 从类注册表取出实现类并实例化（内部工具）。

        创建后若 ``cfg.read_only`` 为 True，自动用 :class:`ReadOnlyClient` 代理包裹，
        使只读配置在经 :meth:`query` / :meth:`execute` 调度时拒绝写操作。

        Args:
            cfg: 数据库配置；必须显式提供 db_type 以便查表。

        Returns:
            绑定该 cfg 的 :class:`RdbClient` 实例（可能被只读代理包裹）。

        Raises:
            ValueError: ``cfg.db_type`` 为空、或该类型未注册时抛出。
        """
        db_type = cfg.db_type
        if not db_type:
            raise ValueError(
                "通过 RdbCfg 创建实例时必须显式提供 cfg.db_type，"
                "以便从类注册表查找对应的 RdbClient 实现类"
            )
        key = db_type.lower()
        with self._lock:
            client_cls = self._class_registry.get(key)
            if client_cls is None:
                registered = ", ".join(self._class_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到 db_type={db_type!r} 对应的 RdbClient 实现类，"
                    f"已注册的类型: {registered}；请先通过 register_client_class() 注册"
                )
        return self._maybe_wrap_read_only(client_cls(cfg))

    def register_client_class(self, client_cls: type[RdbClient]) -> None:
        """
        注册或替换一个 :class:`RdbClient` 实现类（按类自身的 :attr:`~RdbClient.db_type` 归档）。

        注册后即可通过 :meth:`register` 传入 :class:`RdbCfg`，
        由本管理器根据 ``cfg.db_type`` 工厂化创建实例，调用方无需手动 ``new``。

        Args:
            client_cls: :class:`RdbClient` 的具体子类（类对象，非实例）。

        Raises:
            TypeError: 传入的不是 :class:`RdbClient` 子类时抛出。
            ValueError: 类的 :attr:`~RdbClient.db_type` 为空时抛出。
        """
        if not (isinstance(client_cls, type) and issubclass(client_cls, RdbClient)):
            raise TypeError(
                f"register_client_class 仅接受 RdbClient 的子类，"
                f"收到: {client_cls!r}"
            )
        db_type = getattr(client_cls, 'db_type', None)
        if not isinstance(db_type, str) or not db_type:
            raise ValueError(
                f"{client_cls.__name__}.db_type 必须是非空字符串，"
                f"当前值: {db_type!r}"
            )
        key = db_type.lower()
        with self._lock:
            self._class_registry[key] = client_cls

    def unregister_client_class(self, db_type: str) -> bool:
        """
        取消注册指定类型的 :class:`RdbClient` 实现类。

        Args:
            db_type: 数据库类型标识（大小写不敏感）。

        Returns:
            是否成功移除。
        """
        if not isinstance(db_type, str) or not db_type:
            return False
        key = db_type.lower()
        with self._lock:
            if key in self._class_registry:
                del self._class_registry[key]
                return True
            return False

    def get_client_class(self, db_type: str) -> type[RdbClient]:
        """
        获取指定数据库类型的 :class:`RdbClient` 实现类。

        Args:
            db_type: 数据库类型标识（大小写不敏感）。

        Returns:
            :class:`RdbClient` 子类。

        Raises:
            ValueError: 该类型未注册时抛出。
        """
        if not isinstance(db_type, str) or not db_type:
            raise ValueError("db_type 不能为空")
        key = db_type.lower()
        with self._lock:
            client_cls = self._class_registry.get(key)
            if client_cls is None:
                registered = ", ".join(self._class_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到 db_type={db_type!r} 对应的 RdbClient 实现类，"
                    f"已注册的类型: {registered}；请先通过 register_client_class() 注册"
                )
            return client_cls

    def get_registered_client_types(self) -> list[str]:
        """
        获取所有已注册（即支持工厂化创建）的数据库类型列表。

        Returns:
            数据库类型标识列表。
        """
        with self._lock:
            return list(self._class_registry.keys())

    # endregion

    # region ======== 实例注册表（name -> RdbClient 实例） ========

    def _resolve_name(self, name: str | None) -> str:
        """
        将名称解析为注册表键：为空时回落到 :attr:`DEFAULT_NAME`。
        """
        return name if name else self.DEFAULT_NAME

    def register(self, name: str, rdb_client: RdbClient | RdbCfg) -> None:
        """
        注册或替换指定名称的客户端实例。

        第二个参数支持两种形式：

          - :class:`RdbClient` 实例：直接按名称归档（不依赖类注册表）；
          - :class:`RdbCfg` 配置：按 ``cfg.db_type`` 从类注册表取出实现类，自动 ``cls(cfg)`` 工厂化创建实例后归档。
            此时要求对应实现类已通过 :meth:`register_client_class` 注册，且 ``cfg.db_type`` 不能为空。

        无论哪种形式，若配置 ``read_only=True``，均自动用 :class:`ReadOnlyClient` 代理包裹
        （见 :meth:`_maybe_wrap_read_only`），使注册的只读实例具备客户端层写拦截。

        Args:
            name: 实例名称（别名）；为空时使用 :attr:`DEFAULT_NAME`。
            rdb_client: :class:`RdbClient` 实例，或 :class:`RdbCfg` 配置。

        Raises:
            ValueError: 传入 :class:`RdbCfg` 但 ``db_type`` 为空、或对应类型
                未注册时抛出。
        """
        key = self._resolve_name(name)
        if isinstance(rdb_client, RdbCfg):
            client = self._create_client_from_cfg(rdb_client)   # 已含只读包裹
        else:
            client = self._maybe_wrap_read_only(rdb_client)     # 预构造实例同样包裹
        with self._lock:
            self._client_registry[key] = client

    def unregister(self, name: str | None = None) -> bool:
        """
        取消注册指定名称的客户端实例。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            是否成功移除。
        """
        key = self._resolve_name(name)
        with self._lock:
            if key in self._client_registry:
                del self._client_registry[key]
                return True
            return False

    def get_client(self, name: str | None = None) -> RdbClient:
        """
        获取指定名称的客户端实例。

        若按名称未找到且已设置 :attr:`_config_loader`，会先调用配置加载器
        （传入 manager 自身与请求的 name），再重新查找；仍未找到则抛出异常。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            :class:`RdbClient` 实例。

        Raises:
            ValueError: 该名称尚未注册且配置加载器未能成功加载时抛出。
        """
        key = self._resolve_name(name)
        with self._lock:
            client = self._client_registry.get(key)
            if client is None and self._config_loader is not None:
                self._config_loader(self, key)
                client = self._client_registry.get(key)
            if client is None:
                registered = ", ".join(self._client_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到实例 '{key}'，已注册的实例: {registered}；"
                    f"请先通过 register() 注册"
                )
            return client

    def get_registered_names(self) -> list[str]:
        """
        获取所有已注册的实例名称列表。

        Returns:
            实例名称列表。
        """
        with self._lock:
            return list(self._client_registry.keys())

    # endregion

    # region ======== 执行便捷方法（透传 RdbClient） ========

    def get_connection(self, name: str | None = None):
        """
        打开并返回指定实例的数据库连接（透传 :meth:`RdbClient.get_connection`）。

        .. warning::
            若该实例为只读客户端（:class:`ReadOnlyClient`），返回的裸连接**绕过客户端层写拦截**
            （写拦截仅在 :meth:`query` / :meth:`execute` 调度链内生效）。本方法对此仅打一条
            warning 日志提醒，不阻止调用；确权请依赖数据库账号权限。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            数据库连接对象。
        """
        client = self.get_client(name)
        if isinstance(client, ReadOnlyClient):
            log.warning(
                "实例 '%s' 为只读客户端，get_connection 返回的裸连接绕过写拦截，"
                "请确认后续操作确实只读", self._resolve_name(name)
            )
        return client.get_connection()

    def query(self, sql: str, params: tuple[Any, ...] | None = None,
              converters: dict[type, Callable[[Any], Any]] | None = None,
              name: str | None = None) -> list[dict]:
        """
        执行查询（透传 :meth:`RdbClient.query`）。

        Args:
            sql: SQL 查询语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。
            converters: 值转换器映射，``{原始类型: 转换函数}``，由调用方按需传入；
                ``None`` 表示不做任何转换，保持驱动返回的原始类型。
                转换先按值的**精确类型**（:func:`type`）匹配，未命中则沿其 MRO 回退查父类，
                兼容驱动返回的子类型；``None`` 值不参与转换。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            查询结果列表，每个元素是一个字典，键为列名。
        """
        return self.get_client(name).query(sql, params, converters)

    def execute(self, sql: str, params: tuple[Any, ...] | None = None,
                name: str | None = None) -> int:
        """
        执行 SQL 语句（透传 :meth:`RdbClient.execute`）。

        Args:
            sql: SQL 语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            受影响的行数。
        """
        return self.get_client(name).execute(sql, params)

    # endregion


# endregion
