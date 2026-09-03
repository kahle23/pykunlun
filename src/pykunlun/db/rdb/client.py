"""
关系型数据库驱动策略抽象基类。

:class:`RdbClient` 绑定一份 :class:`RdbCfg` 配置，把"因数据库而异"的差异收敛为
可覆盖的钩子方法，把"放之四海皆准"的执行逻辑统一写在本类。新增一种数据库
= 继承本类并覆盖少数钩子，基类的执行逻辑无需改动。
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from types import ModuleType
from typing import Any

from pykunlun.util import logutil, validation

from .cfg import RdbCfg

log = logutil.getLogger(__name__)


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

    def _normalize_rows(self, cursor: Any, rows: list[Any],
                        converters: dict[type, Callable[[Any], Any]] | None = None) -> list[dict[str, Any]]:
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
        def _convert(d: dict[str, Any]) -> dict[str, Any]:
            if not converters:
                return d
            result: dict[str, Any] = {}
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
    def get_driver(self) -> ModuleType:
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

    def is_connection_open(self, connection: Any) -> bool:
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

    def get_connection(self) -> Any:
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
              converters: dict[type, Callable[[Any], Any]] | None = None) -> list[dict[str, Any]]:
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
