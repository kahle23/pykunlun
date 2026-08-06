"""
关系型数据库驱动子包。

提供驱动策略抽象（:class:`RdbClient`、:class:`RdbManager`、:class:`RdbCfg`）、
只读客户端代理（:class:`RdbReadOnlyClient`），以及基于标准库的内置实现
（:class:`SqliteClient`）。各抽象的具体实现由上层包提供。

模块组织：

  - :mod:`pykunlun.db.rdb.cfg`           — 连接配置 :class:`RdbCfg`
  - :mod:`pykunlun.db.rdb.client`        — 驱动策略抽象基类 :class:`RdbClient`
  - :mod:`pykunlun.db.rdb.readonly`      — 只读客户端代理 :class:`RdbReadOnlyClient`
  - :mod:`pykunlun.db.rdb.manager`       — 驱动客户端管理器 :class:`RdbManager`
  - :mod:`pykunlun.db.rdb.sqlite_client` — SQLite 内置驱动 :class:`SqliteClient`
"""

from .cfg import RdbCfg
from .client import RdbClient
from .manager import RdbManager
from .readonly import RdbReadOnlyClient
from .sqlite_client import SqliteClient

__all__ = [
    'RdbCfg',
    'RdbClient',
    'RdbManager',
    'RdbReadOnlyClient',
    'SqliteClient',
]
