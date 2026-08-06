"""
关系型数据库连接配置。

封装数据库连接所需的所有参数，支持多种数据库类型。``RdbCfg`` 为纯数据容器，
自身不做任何校验；各数据库的校验与默认值补全交由
:meth:`pykunlun.db.rdb.client.RdbClient._validate_and_prepare_cfg`
在构造客户端实例时自动完成。
"""

from dataclasses import dataclass


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
                自动用 :class:`RdbReadOnlyClient` 代理包裹真实客户端，于客户端层解析 SQL 首动词，
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
