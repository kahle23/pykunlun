"""
关系型数据库操作的底层抽象模块，定义驱动策略接口与注册表。

采用策略模式：
  - :class:`RdbClient` 为抽象基类，绑定一个 :class:`RdbCfg` 配置，
    定义跨数据库类型的统一接口（含 SQL 执行）；
  - 各数据库具体实现（如 MySQL、PostgreSQL）由上层包提供；
    其中基于标准库的实现（如 SQLite）可直接置于本包内；
  - 通过 :class:`RdbManager` 维护 ``db_type -> 客户端类`` 注册表，
    并按 :meth:`RdbCfg.db_type` 工厂化创建绑定实例。

本模块提供抽象与注册表，并为无第三方依赖的部分（SQLite）提供内置实现。
依赖第三方驱动的实现（如 MySQL 的 pymysql、PostgreSQL 的 psycopg2）
由上层包在导入时通过 :meth:`RdbManager.register` 注册客户端实例。

仅依赖 Python 标准库与 kunlun 自身工具模块。
"""

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from kunlun import loadutil, logutil, validation

log = logutil.getLogger(__name__)


# region ======== 连接配置 ========

@dataclass
class RdbCfg:
    """
    SQL 数据库连接配置。

    封装数据库连接所需的所有参数，支持多种数据库类型。
    所有连接字段均提供默认值，便于仅需少量参数的数据库（如 SQLite，
    仅需 :attr:`database` 指定文件路径）使用。

    Attributes:
        host: 服务器地址。
        port: 服务端口。
        username: 用户名。
        password: 密码。
        database: 数据库名称；对文件型数据库（如 SQLite）为数据库文件路径。
        db_type: 数据库类型标识，如 ``mysql``、``postgresql``、``sqlite``，默认为 mysql。
        charset: 数据库字符集，默认 utf8mb4（MySQL 默认值；PostgreSQL 建议使用 utf8）。
        validation_query: 连接探活 SQL，默认 ``SELECT 1``。

    校验策略：RdbCfg 仅做粗校验（构造时通过 ``__post_init__`` 保证 db_type 非空）；
    各数据库的细校验（必填字段差异）交由 :meth:`RdbClient.validate_cfg` 在构造
    客户端实例时自动完成。
    """

    host: str = ''
    port: int = 0
    username: str = ''
    password: str = ''
    database: str = ''
    db_type: str = 'mysql'
    charset: str = 'utf8mb4'
    validation_query: str = 'SELECT 1'

    def __post_init__(self) -> None:
        """
        粗校验：仅保证 :attr:`db_type` 非空。

        各数据库的细校验（必填字段差异，如 SQLite 仅需 database）由
        :meth:`RdbClient.validate_cfg` 在构造客户端实例时自动完成，
        RdbCfg 层不感知具体数据库类型。

        Raises:
            ValueError: db_type 为空时抛出。
        """
        validation.check_required_fields_not_empty(self, ['db_type'], '数据库配置')

    @staticmethod
    def load_from_json_cfg(config_path: Union[str, Path]) -> 'RdbCfg':
        """
        从 JSON 文件加载数据库配置。

        读取指定路径的 JSON 文件并返回 RdbCfg 实例。构造时由 ``__post_init__``
        完成粗校验（db_type 非空）；各数据库的细校验延迟到构造
        :class:`RdbClient` 实例时由 :meth:`RdbClient.validate_cfg` 触发。

        Args:
            config_path: JSON 配置文件路径，支持字符串或 Path 对象。

        Returns:
            RdbCfg 实例对象。

        Raises:
            FileNotFoundError: 文件不存在时抛出。
            ValueError: db_type 缺失或配置格式不符时抛出。
            json.JSONDecodeError: JSON 格式解析失败时抛出。
        """
        return loadutil.load_dataclass_from_json_file(config_path, RdbCfg)


# endregion


# region ======== 驱动策略抽象基类 ========

class RdbClient(ABC):
    """
    关系型数据库驱动策略抽象基类（绑定一份 :class:`RdbCfg` 配置）。

    每个实例绑定一个 :class:`RdbCfg`，把"因数据库而异"的差异收敛为可覆盖的
    钩子方法，把"放之四海皆准"的执行逻辑统一写在本类。新增一种数据库 =
    继承本类并覆盖少数钩子，基类的执行逻辑无需改动。

    方法分两层：

    【配置层】—— 构造时即校验
      - ``db_type``             : 数据库类型标识（取自绑定的 cfg）。
      - ``validate_cfg``        : 校验 cfg 必填字段；默认按网络库校验
                                  host/port/username/password/database 及端口范围，
                                  文件型数据库（如 SQLite）可覆盖为只校验 database。
                                  由 ``__init__`` 自动调用，**构造即校验**。

    【驱动差异钩子】—— 子类按需实现/覆盖
      - ``get_driver``          : 返回驱动模块对象（pymysql/psycopg2/sqlite3...）。
      - ``build_connect_kwargs``: 返回 ``connect()`` 的关键字参数
                                  （charset vs client_encoding vs database）。
      - ``is_connection_open``  : 判断连接是否可用（pymysql 的 ``.open`` /
                                  psycopg2 的 ``.closed``），默认鸭子类型。
      - ``normalize_rows``      : 把游标结果统一整形成 ``list[dict]``，
                                  兼容字典游标与元组游标。

    【通用执行接口】—— 基类实现，组合上述钩子，调用方直接使用
      - ``get_connection``      : ``get_driver().connect(**build_connect_kwargs())``
      - ``query``               : ``get_connection → execute → fetchall → normalize_rows``
      - ``execute``             : ``get_connection → execute → commit/rollback``
      - ``close``               : 释放底层连接资源（默认空操作，连接池子类覆盖）

    调用链示意::

        client.query(sql)
          └─ get_connection()
               ├─ get_driver()                              # 钩子
               └─ driver.connect(**build_connect_kwargs())  # 钩子
          └─ cursor.execute / fetchall                      # DB-API 2.0，各驱动一致
          └─ normalize_rows(...)                            # 钩子

    其中 :meth:`get_driver`、:meth:`build_connect_kwargs` 为必须实现的抽象方法，
    其余方法提供跨驱动通用的默认实现，可按需覆盖。

    通常直接构造使用（构造时自动细校验），也可注册到 :class:`RdbManager` 按名称管理::

        client = SomeRdbClient(cfg)
        client.execute("INSERT INTO t VALUES (?, ?)", (1, 'a'))
        rows = client.query("SELECT * FROM t")
    """

    def __init__(self, cfg: RdbCfg) -> None:
        """
        Args:
            cfg: 绑定的数据库配置对象。

        Raises:
            ValueError: 配置校验不通过时抛出（由 :meth:`validate_cfg` 触发）。
        """
        self.cfg = cfg
        # 构造即校验：不同数据库的必填字段不同，交由各实现的 validate_cfg 判定
        self.validate_cfg()

    @property
    def db_type(self) -> str:
        """
        当前实例的数据库类型标识，取自绑定的 :attr:`cfg`。
        """
        return self.cfg.db_type

    def validate_cfg(self) -> None:
        """
        校验绑定的 :attr:`cfg` 所需字段。

        不同数据库所需字段不同（如文件型数据库仅需 database），故本方法为
        **实例方法**，由各实现按自身规则覆盖。默认实现按网络型数据库校验
        通用必填字段（host、port、username、password、database）及端口范围。

        本方法由 :meth:`__init__` 自动调用，确保构造出的实例配置一定有效。

        Raises:
            ValueError: 必填字段为空、或端口越界时抛出。
        """
        validation.check_required_fields_not_empty(
            self.cfg, ['host', 'port', 'username', 'password', 'database'], '数据库配置')
        if not (1 <= self.cfg.port <= 65535):
            raise ValueError(f"端口号必须在 1-65535 范围内，当前值: {self.cfg.port}")

    @abstractmethod
    def get_driver(self):
        """
        获取数据库驱动模块。

        各实现自报模块名与 pip 安装名，未安装时建议通过
        :func:`kunlun.modutil.import_module` 自动安装。

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

        默认按鸭子类型处理常见驱动：优先取 ``open`` 属性（如 pymysql），
        否则取 ``closed`` 属性取反（如 psycopg2）。驱动既无 ``open`` 也无
        ``closed`` 属性时乐观返回 True，由实际执行时的错误驱动重连。

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

    def normalize_rows(self, cursor, rows: List, to_float: bool = False) -> List[Dict]:
        """
        将查询结果行整形为字典列表。

        兼容两类游标：
          - 字典游标（如 pymysql DictCursor）：行本身为 dict，直接按键取值；
          - 元组游标（如 psycopg2、sqlite3）：通过 ``cursor.description`` 取列名
            与行 zip 成字典；无法获取列名时回退为 ``field_0``、``field_1``...。

        Args:
            cursor: 已执行查询的游标，用于获取 ``description``。
            rows: ``cursor.fetchall()`` 返回的行列表。
            to_float: 是否将 Decimal 类型转换为 float，默认 False。

        Returns:
            字典列表，每个字典表示一行，键为列名。
        """
        if not rows:
            return []

        def _convert(d: Dict) -> Dict:
            if not to_float:
                return d
            return {
                k: float(v) if isinstance(v, Decimal) else v
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

    def get_connection(self):
        """
        打开并返回一个新的数据库连接。

        基于绑定的 :attr:`cfg`，经 :meth:`get_driver` 与 :meth:`build_connect_kwargs`
        建立连接，**连接的生命周期由调用方负责**（使用后需关闭）。

        本方法每次调用都创建新连接，不维护连接池；需要连接复用的上层模块
        （如带连接池的客户端）可基于本方法或直接使用驱动自行管理。

        注意：对 SQLite 的 ``:memory:`` 内存库，每次调用得到的是彼此隔离的
        独立内存库，跨连接不可见数据；如需跨语句共享数据，请使用文件库，
        或由调用方持有同一个连接。

        Returns:
            数据库连接对象。
        """
        driver = self.get_driver()
        return driver.connect(**self.build_connect_kwargs())

    def query(self, sql: str, params: Optional[Tuple[Any, ...]] = None,
              to_float: bool = False) -> List[Dict]:
        """
        执行查询并返回结果（自动管理连接生命周期）。

        适用于 SELECT 等读操作，结果经 :meth:`normalize_rows` 整形为字典列表。
        内部每次创建并关闭一个连接（connect-per-call）。

        Args:
            sql: SQL 查询语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。
            to_float: 是否将 Decimal 类型转换为 float，默认 False。

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
            return self.normalize_rows(cursor, rows, to_float)
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


# region ======== 驱动客户端管理器（注册表） ========

class RdbManager:
    """
    关系型数据库驱动客户端管理器（按名称注册 :class:`RdbClient` 实例）。

    维护 ``name -> RdbClient 实例`` 的注册表，每个实例绑定一份 :class:`RdbCfg`。
    同一管理器可注册多份不同配置的实例，通过名称（别名）区分。

    关于 ``name`` 的用途：name 是注册实例的**别名**，用于区分同一数据库类型
    的不同连接配置，而非区分数据库类型本身。典型场景是按环境隔离——例如为开发
    环境与测试环境各注册一个 :class:`MysqlRdbClient` 实例（实现类相同、连接配置
    不同），通过 ``name="dev"`` / ``name="test"`` 分别访问；也可按业务模块命名
    （如 ``"order_db"``、``"user_db"``）。

    :attr:`DEFAULT_NAME` 为默认实例名称。除 :meth:`register` 必须显式提供名称外，
    其余方法（:meth:`unregister`、:meth:`get_client`、:meth:`get_connection`、
    :meth:`query`、:meth:`execute`）的 ``name`` 参数均可省略，省略时使用默认名称。

    本类额外提供 :meth:`get_connection`、:meth:`query`、:meth:`execute` 便捷方法，
    比直接调用 :class:`RdbClient` 同名方法多一个 ``name`` 参数（用于选择已注册
    的实例），其余参数语义一致。

    用法示例::

        manager = RdbManager()

        # 构造客户端实例并注册（register 需显式提供 name）
        client = SqliteRdbClient(cfg)
        manager.register("default", client)

        # 通过管理器直接执行（name 可省略，默认 "default"）
        manager.execute("INSERT INTO t VALUES (1)")
        rows = manager.query("SELECT * FROM t")

        # 指定 name 操作非默认实例
        manager.execute("...", name="other")
    """

    #: 默认实例名称
    DEFAULT_NAME = "default"

    def __init__(self) -> None:
        # 实例注册表：name -> RdbClient 实例（本实例独有）
        self._registry: Dict[str, RdbClient] = {}
        self._lock = threading.RLock()

    def _resolve_name(self, name: Optional[str]) -> str:
        """
        将名称解析为注册表键：为空时回落到 :attr:`DEFAULT_NAME`。
        """
        return name if name else self.DEFAULT_NAME

    def register(self, name: str, rdb_client: RdbClient) -> None:
        """
        注册或替换指定名称的客户端实例。

        Args:
            name: 实例名称（别名）；为空时使用 :attr:`DEFAULT_NAME`。
            rdb_client: :class:`RdbClient` 实例。
        """
        key = self._resolve_name(name)
        with self._lock:
            self._registry[key] = rdb_client

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
            if key in self._registry:
                del self._registry[key]
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
            client = self._registry.get(key)
            if client is None:
                registered = ", ".join(self._registry.keys()) or "（无）"
                raise ValueError(
                    f"未找到实例 '{key}'，已注册的实例: {registered}；"
                    f"请先通过 register() 注册"
                )
            return client

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
              to_float: bool = False, name: Optional[str] = None) -> List[Dict]:
        """
        执行查询（透传 :meth:`RdbClient.query`）。

        Args:
            sql: SQL 查询语句字符串。
            params: SQL 参数，用于参数化查询，防止 SQL 注入。
            to_float: 是否将 Decimal 类型转换为 float，默认 False。
            name: 实例名称，省略时使用 :attr:`DEFAULT_NAME`。

        Returns:
            查询结果列表，每个元素是一个字典，键为列名。
        """
        return self.get_client(name).query(sql, params, to_float)

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

    def get_registered_names(self) -> List[str]:
        """
        获取所有已注册的实例名称列表。

        Returns:
            实例名称列表。
        """
        with self._lock:
            return list(self._registry.keys())


# endregion
