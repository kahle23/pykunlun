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

仅依赖 Python 标准库与 kunlun 自身工具模块。
"""

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, Union

from kunlun.util import logutil, validation

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

    校验策略：RdbCfg 为纯数据容器，自身不做任何校验；
    各数据库的校验与默认值补全（必填字段差异）
    交由 :meth:`RdbClient._validate_and_prepare_cfg` 在构造客户端实例时自动完成。
    """

    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    database: Optional[str] = None
    db_type: Optional[str] = None
    charset: Optional[str] = None
    validation_query: Optional[str] = None


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

    【值转换器管理】—— 实例级注册表，影响 :meth:`query` 的返回值类型
      - ``_default_converters`` : 返回本数据库的默认转换器（子类可覆盖），默认空。
      - ``register_converter``  : 注册/替换一个 ``{类型: 转换函数}``。
      - ``unregister_converter``: 取消注册某类型，返回是否移除。
      - ``get_converter_types`` : 已注册的类型列表。
      - ``get_converter``       : 取某类型的转换函数，未注册返回 ``None``。

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
        # 值转换器注册表：不同数据库驱动返回的原始类型各异，由各实现通过
        # _default_converters 提供初始默认，运行时亦可经 register_converter 增改。
        self._converter_lock = threading.RLock()
        self._converters: Dict[type, Callable[[Any], Any]] = dict(self._default_converters())

    def __setattr__(self, name: str, value: Any) -> None:
        """
        拦截实例属性赋值，保护 :attr:`db_type` 不被运行时篡改。

        单独拦 ``db_type`` 的原因：基类虽把它声明为抽象只读 property，
        但子类为满足抽象约束会用类级常量 ``db_type = 'mysql'`` 覆盖——
        该常量是普通字符串（非 data descriptor），会遮蔽基类 property，使 property 的只读保护失效，
        ``instance.db_type = x`` 将悄悄创建实例级遮蔽。
        本方法显式抛 :class:`AttributeError` 堵住此缺口；其余属性（cfg、连接池参数等）照常赋值。

        Raises:
            AttributeError: 尝试给实例的 ``db_type`` 赋值时抛出。
        """
        if name == 'db_type':
            raise AttributeError(
                f"{type(self).__name__}.db_type 是实现类硬编码的类级常量，"
                f"代表本类所属的数据库类型，禁止运行时修改。"
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

    def _normalize_rows(self, cursor, rows: List,
                        converters: Optional[Dict[type, Callable[[Any], Any]]] = None) -> List[Dict]:
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
                整形时对每个值按其**精确类型**（:func:`type`）查找转换函数并替换为函数返回值；
                未命中的值（含 ``None``）保持原样。``None`` 或空映射表示不转换。

        Returns:
            字典列表，每个字典表示一行，键为列名。
        """
        if not rows:
            return []

        def _convert(d: Dict) -> Dict:
            if not converters:
                return d
            return {
                k: fn(v) if (fn := converters.get(type(v))) else v
                for k, v in d.items()
            }

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

    # region ======== 值转换器管理 ========

    def _default_converters(self) -> Dict[type, Callable[[Any], Any]]:
        """
        返回本数据库类型的默认值转换器。

        不同数据库驱动返回的原始类型各异（如 MySQL 常返回 :class:`~decimal.Decimal`，
        PostgreSQL 可能返回 :class:`list` / :class:`datetime.datetime`，SQLite 按类型亲和性返回原生类型），
        子类可覆盖本方法为常用类型注册默认转换器，使调用方无需每次 :meth:`query` 都显式传 ``converters``。
        基类默认返回空映射（即默认不做任何转换，保持驱动返回的原始类型）。

        返回的字典会被 :meth:`__init__` 拷贝为实例注册表的初始值，故子类返回的字典本身不会被原地修改。

        Returns:
            默认转换器映射 ``{原始类型: 转换函数}``。
        """
        return {}

    def _resolve_converters(self, override: Optional[Dict[type, Callable[[Any], Any]]]
                            ) -> Optional[Dict[type, Callable[[Any], Any]]]:
        """
        解析本次查询的有效转换器，供 :meth:`query` 使用。区分三种调用意图：

        - ``override`` 为 ``None``：沿用实例注册表（含子类 :meth:`_default_converters` 提供的默认）；
          注册表为空时返回 ``None``。
        - ``override`` 为空映射 ``{}``：显式声明本次**不做任何转换**，忽略实例注册表，返回 ``None``。
        - ``override`` 非空：返回 ``{**实例注册表, **override}``（同名类型以 ``override`` 为准）的新字典，
          即在默认基础上合并/覆盖。

        本方法持 :attr:`_converter_lock` 读取，保证读取期间注册表不被并发修改。
        """
        with self._converter_lock:
            if override is None:
                return self._converters or None
            if not override:
                return None
            return {**self._converters, **override}

    def register_converter(self, type_: type, converter: Callable[[Any], Any]) -> None:
        """
        注册或替换一个类型的值转换器。

        注册后，本实例 :meth:`query` 返回结果中该类型（精确匹配，非 :func:`isinstance`）的值
        将自动经 ``converter`` 转换；
        与 :meth:`query` 的 ``converters`` 参数合并时，实例注册表为基础、参数覆盖实例注册表。

        Args:
            type_: 触发转换的原始值类型。
            converter: 转换函数，接收原值返回转换后的值。
        """
        with self._converter_lock:
            self._converters[type_] = converter

    def unregister_converter(self, type_: type) -> bool:
        """
        取消注册某类型的值转换器。

        Args:
            type_: 要移除的原始值类型。

        Returns:
            是否成功移除（未注册时返回 False）。
        """
        with self._converter_lock:
            if type_ in self._converters:
                del self._converters[type_]
                return True
            return False

    def get_converter_types(self) -> List[type]:
        """
        获取已注册的转换器类型列表。

        Returns:
            已注册的原始值类型列表（拷贝，修改不影响内部注册表）。
        """
        with self._converter_lock:
            return list(self._converters.keys())

    def get_converter(self, type_: type) -> Optional[Callable[[Any], Any]]:
        """
        获取某类型的转换器。

        Args:
            type_: 原始值类型。

        Returns:
            该类型的转换函数；未注册返回 ``None``。
        """
        with self._converter_lock:
            return self._converters.get(type_)

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

    @abstractmethod
    def get_driver(self):
        """
        获取数据库驱动模块。

        各实现自报模块名与 pip 安装名，未安装时建议通过 :func:`kunlun.modutil.import_module` 自动安装。

        Returns:
            数据库驱动模块对象。
        """
        pass

    @abstractmethod
    def build_connect_kwargs(self) -> Dict[str, Any]:
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

    def query(self, sql: str, params: Optional[Tuple[Any, ...]] = None,
              converters: Optional[Dict[type, Callable[[Any], Any]]] = None) -> List[Dict]:
        """
        执行查询并返回结果（自动管理连接生命周期）。

        适用于 SELECT 等读操作，结果经 :meth:`_normalize_rows` 整形为字典列表。
        内部每次创建并关闭一个连接（connect-per-call）。

        Args:
            sql: SQL 查询语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。
            converters: 值转换器映射，``{原始类型: 转换函数}``，三态语义：
                - ``None``（默认）：沿用实例注册表（含子类 :meth:`_default_converters` 提供的默认转换）；
                - 空映射 ``{}``：显式声明本次查询**不做任何转换**（忽略注册表）；
                - 非空映射：与实例注册表**合并**，参数优先（同名类型以参数为准）。
                转换按值的**精确类型**（:func:`type`）匹配，未命中（含 ``None`` 值）保持原样。

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
            return self._normalize_rows(cursor, rows, self._resolve_converters(converters))
        except Exception:
            log.exception("数据库查询失败")
            raise
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def execute(self, sql: str, params: Optional[Tuple[Any, ...]] = None) -> int:
        """
        执行 SQL 语句（自动管理连接生命周期）。

        自动提交事务，适用于 INSERT、UPDATE、DELETE 等写操作。
        内部每次创建并关闭一个连接（connect-per-call），异常时回滚。

        Args:
            sql: SQL 语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。

        Returns:
            受影响的行数。
        """
        connection = self.get_connection()
        cursor = None
        try:
            cursor = connection.cursor()
            if params is None:
                cursor.execute(sql)
            else:
                cursor.execute(sql, params)
            connection.commit()
            return int(cursor.rowcount)
        except Exception:
            connection.rollback()
            raise
        finally:
            if cursor:
                cursor.close()
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

    def __init__(self) -> None:
        # 类注册表：db_type -> RdbClient 子类（用于按 cfg.db_type 工厂化创建实例）
        self._class_registry: Dict[str, Type[RdbClient]] = {}
        # 实例注册表：name -> RdbClient 实例（本实例独有）
        self._client_registry: Dict[str, RdbClient] = {}
        self._lock = threading.RLock()

    def _resolve_name(self, name: Optional[str]) -> str:
        """
        将名称解析为注册表键：为空时回落到 :attr:`DEFAULT_NAME`。
        """
        return name if name else self.DEFAULT_NAME

    # endregion

    # region ======== 类注册表（db_type -> RdbClient 子类） ========

    def _create_client_from_cfg(self, cfg: RdbCfg) -> RdbClient:
        """
        按 ``cfg.db_type`` 从类注册表取出实现类并实例化（内部工具）。

        Args:
            cfg: 数据库配置；必须显式提供 db_type 以便查表。

        Returns:
            绑定该 cfg 的 :class:`RdbClient` 实例。

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
        return client_cls(cfg)

    def register_client_class(self, client_cls: Type[RdbClient]) -> None:
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

    def get_client_class(self, db_type: str) -> Type[RdbClient]:
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

    def get_registered_client_types(self) -> List[str]:
        """
        获取所有已注册（即支持工厂化创建）的数据库类型列表。

        Returns:
            数据库类型标识列表。
        """
        with self._lock:
            return list(self._class_registry.keys())

    # endregion

    # region ======== 实例注册表（name -> RdbClient 实例） ========

    def register(self, name: str, rdb_client: Union[RdbClient, RdbCfg]) -> None:
        """
        注册或替换指定名称的客户端实例。

        第二个参数支持两种形式：

          - :class:`RdbClient` 实例：直接按名称归档（不依赖类注册表）；
          - :class:`RdbCfg` 配置：按 ``cfg.db_type`` 从类注册表取出实现类，自动 ``cls(cfg)`` 工厂化创建实例后归档。
            此时要求对应实现类已通过 :meth:`register_client_class` 注册，且 ``cfg.db_type`` 不能为空。

        Args:
            name: 实例名称（别名）；为空时使用 :attr:`DEFAULT_NAME`。
            rdb_client: :class:`RdbClient` 实例，或 :class:`RdbCfg` 配置。

        Raises:
            ValueError: 传入 :class:`RdbCfg` 但 ``db_type`` 为空、或对应类型
                未注册时抛出。
        """
        key = self._resolve_name(name)
        client = (self._create_client_from_cfg(rdb_client)
                  if isinstance(rdb_client, RdbCfg) else rdb_client)
        with self._lock:
            self._client_registry[key] = client

    def unregister(self, name: Optional[str] = None) -> bool:
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

    def get_client(self, name: Optional[str] = None) -> RdbClient:
        """
        获取指定名称的客户端实例。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            :class:`RdbClient` 实例。

        Raises:
            ValueError: 该名称尚未注册时抛出。
        """
        key = self._resolve_name(name)
        with self._lock:
            client = self._client_registry.get(key)
            if client is None:
                registered = ", ".join(self._client_registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到实例 '{key}'，已注册的实例: {registered}；"
                    f"请先通过 register() 注册"
                )
            return client

    def get_registered_names(self) -> List[str]:
        """
        获取所有已注册的实例名称列表。

        Returns:
            实例名称列表。
        """
        with self._lock:
            return list(self._client_registry.keys())

    # endregion

    # region ======== 执行便捷方法（透传 RdbClient） ========

    def get_connection(self, name: Optional[str] = None):
        """
        打开并返回指定实例的数据库连接（透传 :meth:`RdbClient.get_connection`）。

        Args:
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            数据库连接对象。
        """
        return self.get_client(name).get_connection()

    def query(self, sql: str, params: Optional[Tuple[Any, ...]] = None,
              converters: Optional[Dict[type, Callable[[Any], Any]]] = None,
              name: Optional[str] = None) -> List[Dict]:
        """
        执行查询（透传 :meth:`RdbClient.query`）。

        Args:
            sql: SQL 查询语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。
            converters: 值转换器映射，``{原始类型: 转换函数}``，三态语义：
                - ``None``（默认）：沿用实例注册表（含子类 :meth:`_default_converters` 提供的默认转换）；
                - 空映射 ``{}``：显式声明本次查询**不做任何转换**（忽略注册表）；
                - 非空映射：与实例注册表**合并**，参数优先（同名类型以参数为准）。
                转换按值的**精确类型**（:func:`type`）匹配，未命中（含 ``None`` 值）保持原样。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            查询结果列表，每个元素是一个字典，键为列名。
        """
        return self.get_client(name).query(sql, params, converters)

    def execute(self, sql: str, params: Optional[Tuple[Any, ...]] = None,
                name: Optional[str] = None) -> int:
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
